"""Bounded JSON logs and private, failure-tolerant rotating handlers."""

from __future__ import annotations

import json
import logging
import os
import re
import stat
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TextIO

from bridge.diagnostic_events import clean_fields, diagnostic_context

_SOURCE_ROOT = Path(os.path.abspath(__file__)).parent
_SECRET_NAME = re.compile(r"token|secret|password|credential|api.?key|authorization", re.I)
_AUTH = re.compile(r"(?i)(?:bearer|basic|tma)\s+[^\s,;]+")
_QUERY = re.compile(
    r"(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|authorization)[\"']?\s*[:=]\s*[\"']?)[^\s\"',;&}]+"
)
_BOT = re.compile(r"(?:bot)?\d{6,}:[A-Za-z0-9_-]{20,}")
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")


def configured_secrets(environ: Mapping[str, object]) -> tuple[str, ...]:
    return tuple(
        value
        for key, value in environ.items()
        if _SECRET_NAME.search(key) and isinstance(value, str) and 8 <= len(value) <= 8192
    )[:128]


def logging_options(environ: Mapping[str, object]) -> tuple[int, int, int]:
    try:
        mib = int(str(environ.get("SILLYTAVERN_LOG_MAX_MIB", "10")))
        backups = int(str(environ.get("SILLYTAVERN_LOG_BACKUPS", "5")))
    except (TypeError, ValueError):
        raise ValueError("Log size and backup count must be integers") from None
    level = str(environ.get("SILLYTAVERN_LOG_LEVEL", "INFO")).upper()
    if not 1 <= mib <= 100 or not 1 <= backups <= 10 or level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise ValueError("Logs require 1-100 MiB, 1-10 backups, and DEBUG/INFO/WARNING/ERROR level")
    return mib * 1024 * 1024, backups, int(getattr(logging, level))


class DiagnosticFormatter(logging.Formatter):
    def __init__(self, secrets_to_hide: Sequence[str] = ()) -> None:
        super().__init__()
        self._secrets = tuple(sorted((s for s in secrets_to_hide if s), key=len, reverse=True))[:128]

    def redact(self, value: str) -> str:
        for secret in self._secrets:
            value = value.replace(secret, "[redacted]")
        value = _AUTH.sub("[redacted authorization]", value)
        value = _QUERY.sub(r"\1[redacted]", value)
        value = _BOT.sub("[redacted bot token]", value)
        return _JWT.sub("[redacted token]", value)

    def format(self, record: logging.LogRecord) -> str:
        # Only bridge call sites are subject to the mandatory static-template
        # inventory. A third-party/root caller can already have interpolated
        # arbitrary dialogue into record.msg, so never copy its text or name.
        trusted_source = Path(os.path.abspath(record.pathname)).is_relative_to(_SOURCE_ROOT)
        data: dict[str, object] = {
            "schema": 1,
            "timestamp": datetime.fromtimestamp(record.created, UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": self.redact(record.name)[:100] if trusted_source else "external",
            **diagnostic_context(),
        }
        fields = getattr(record, "diagnostic_fields", None)
        if isinstance(fields, dict):
            data.update(clean_fields(fields))
            name = fields.get("event", "diagnostic.invalid_event")
            data["event"] = (
                name
                if isinstance(name, str) and re.fullmatch(r"[a-z][a-z0-9_.]{0,79}", name)
                else "diagnostic.invalid_event"
            )
        else:
            data["event"] = "runtime.log"
            # Bridge legacy arguments are deliberately omitted; CI inventories
            # each production template and rejects dynamic/preformatted calls.
            if not trusted_source:
                data["message"] = "[external log details omitted]"
            elif isinstance(record.msg, str):
                data["message"] = self.redact(record.msg)[:1024]
            else:
                data["message"] = "[non-text log]"
            data["line"] = record.lineno
        if record.exc_info and record.exc_info[0]:
            data["error_type"] = record.exc_info[0].__name__[:80]
            frames = []
            tb = record.exc_info[2]
            while tb is not None:
                frames.append(
                    {
                        "file": Path(tb.tb_frame.f_code.co_filename).name[:80],
                        "function": tb.tb_frame.f_code.co_name[:80],
                        "line": tb.tb_lineno,
                    }
                )
                frames = frames[-12:]
                tb = tb.tb_next
            data["traceback"] = frames
        # Recheck safe metadata too: a credential could appear in a model name.
        for key, value in data.items():
            if isinstance(value, str):
                data[key] = self.redact(value)
        rendered = json.dumps(data, ensure_ascii=True, separators=(",", ":"))
        if len(rendered) > 8192:
            rendered = json.dumps(
                {
                    "schema": 1,
                    "timestamp": data["timestamp"],
                    "level": "WARNING",
                    "event": "diagnostic.record_truncated",
                }
            )
        return rendered


class PrivateRotatingHandler(RotatingFileHandler):
    """Protect every file creation, including reopen after rollover."""

    dropped = 0

    def _open(self) -> TextIO:
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.baseFilename, flags, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError("Log target must be a private regular file")
            os.fchmod(fd, 0o600)
            return os.fdopen(fd, "a", encoding="utf-8")
        except BaseException:
            os.close(fd)
            raise

    def handleError(self, record: logging.LogRecord) -> None:
        # logging's default error reporter prints the original unsafe arguments.
        self.dropped += 1


class DiagnosticConsoleHandler(logging.StreamHandler):
    bridge_console = True
    dropped = 0

    def handleError(self, record: logging.LogRecord) -> None:
        self.dropped += 1
