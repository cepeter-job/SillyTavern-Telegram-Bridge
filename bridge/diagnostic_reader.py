"""Bounded, read-only, session-filtered diagnostics; never expose legacy log text."""

from __future__ import annotations

import json
import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bridge.diagnostic_events import clean_fields, scope_reference

_MAX_BYTES = 2 * 1024 * 1024
_MAX_FILE_BYTES = 256 * 1024
_MAX_EVENTS = 500
_MAX_RECORD = 8192
_EVENT = re.compile(r"[a-z][a-z0-9_.]{0,79}\Z")
_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
_COUNTS = ("input_tokens", "output_tokens", "total_tokens", "cached_tokens", "reasoning_tokens")


def _tail(path: Path, budget: int) -> tuple[bytes, int, bool, bool]:
    """Open only a regular, single-link file, without blocking on a substituted FIFO."""
    fd = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        if path.is_symlink():
            return b"", 0, False, False
        fd = os.open(path, flags)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            return b"", 0, False, False
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            return b"", 0, False, False
        wanted = min(budget, _MAX_FILE_BYTES, info.st_size)
        offset = max(0, info.st_size - wanted)
        os.lseek(fd, offset, os.SEEK_SET)
        raw = os.read(fd, wanted)
        scanned = len(raw)
        truncated = offset > 0
        if offset:
            raw = raw.partition(b"\n")[2]
        if raw and not raw.endswith(b"\n"):
            truncated = True
            raw = raw.rpartition(b"\n")[0]
        return raw, scanned, truncated, True
    except OSError:
        return b"", 0, False, False
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def _record(raw: bytes, chat_ref: str, session_ref: str) -> dict[str, Any] | None:
    if not raw or len(raw) > _MAX_RECORD:
        return None
    try:
        item = json.loads(raw)
        if not isinstance(item, dict) or item.get("schema") != 1:
            return None
        name = item.get("event")
        if not isinstance(name, str) or not _EVENT.fullmatch(name) or name == "runtime.log":
            return None
        if item.get("chat_ref") != chat_ref or item.get("session_ref") != session_ref:
            return None
        level = item.get("level")
        timestamp = item.get("timestamp")
        if not isinstance(level, str) or level not in _LEVELS or not isinstance(timestamp, str):
            return None
        if len(timestamp) > 40:
            return None
        when = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if when.tzinfo is None:
            return None
        fields = clean_fields({key: value for key, value in item.items() if key not in {"chat_id", "session_id"}})
        return {
            **fields,
            "event": name,
            "level": level,
            "timestamp": when.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        }
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        return None


def _unfinished(events: list[dict[str, Any]]) -> int:
    finished = {
        (item.get("call_id"), item.get("attempt"))
        for item in events
        if item["event"] == "provider.finish" and item.get("call_id")
    }
    starts = [item for item in events if item["event"] == "provider.start"]
    return sum(not item.get("call_id") or (item.get("call_id"), item.get("attempt")) not in finished for item in starts)


def _usage(events: list[dict[str, Any]], *, truncated: bool) -> dict[str, Any]:
    attempts = [item for item in events if item["event"] == "provider.finish"]
    unfinished = _unfinished(events)
    result: dict[str, Any] = {
        "attempts": len(attempts) + unfinished,
        "completed_attempts": len(attempts),
        "unfinished_attempts": unfinished,
        "reported_attempts": sum(item.get("usage_reported") is True for item in attempts),
        "complete": bool(attempts)
        and not truncated
        and not unfinished
        and all(item.get("usage_complete") is True for item in attempts),
    }
    for field in _COUNTS:
        values = [item[field] for item in attempts if type(item.get(field)) is int]
        result[field] = sum(values) if values else None
    return result


def _traces(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(events):
        request_id = item.get("request_id", "")
        key = request_id or item.get("call_id") or f"unlinked-{index}"
        group = groups.setdefault(key, {"request_id": request_id, "events": 0, "failures": 0, "fallbacks": 0})
        group["events"] += 1
        if item.get("status") in {"failed", "rejected", "cancelled"} or _LEVELS[item["level"]] >= 30:
            group["failures"] += 1
        if item["event"] == "provider.fallback" and item.get("status") == "selected":
            group["fallbacks"] += 1
        group.update(last_event=item["event"], last_status=item.get("status", ""), timestamp=item["timestamp"])
    for key, group in groups.items():
        related = [item for item in events if (item.get("request_id") or item.get("call_id")) == key]
        group["unfinished_attempts"] = _unfinished(related)
        group["outcome"] = "unknown" if group["unfinished_attempts"] else group["last_status"]
    return list(reversed(groups.values()))


def read_events(
    log_file: Path,
    *,
    chat_id: str,
    session_id: str,
    request_id: str = "",
    purpose: str = "",
    level: str = "DEBUG",
    limit: int = 200,
    max_bytes: int = _MAX_BYTES,
) -> dict[str, Any]:
    """Read fixed numbered rotations only; the caller supplies a server-authorized scope."""
    limit = max(1, min(_MAX_EVENTS, int(limit)))
    budget = max(1, min(_MAX_BYTES, int(max_bytes)))
    minimum = _LEVELS.get(level, _LEVELS["DEBUG"])
    chat_ref = scope_reference("chat", chat_id)
    session_ref = scope_reference("session", session_id)
    events: list[dict[str, Any]] = []
    scanned = skipped = 0
    truncated = available = False
    path = Path(log_file)
    for rotation in range(11):
        remaining = budget - scanned
        if remaining <= 0:
            truncated = True
            break
        candidate = path if rotation == 0 else path.with_name(f"{path.name}.{rotation}")
        raw, count, cut, opened = _tail(candidate, remaining)
        scanned += count
        truncated = truncated or cut
        available = available or opened
        for line in reversed(raw.splitlines()):
            item = _record(line, chat_ref, session_ref)
            if item is None:
                skipped += 1
                continue
            if (request_id and item.get("request_id") != request_id) or (purpose and item.get("purpose") != purpose):
                continue
            if _LEVELS[item["level"]] < minimum:
                continue
            if len(events) == limit:
                truncated = True
                break
            events.append(item)
        if len(events) == limit:
            truncated = True
            break
    events.reverse()
    purposes = sorted(
        {
            str(item.get("purpose", "unscoped"))
            for item in events
            if item["event"] in {"provider.start", "provider.finish"}
        }
    )
    return {
        "events": events,
        "traces": _traces(events),
        "usage": _usage(events, truncated=truncated),
        "usage_by_purpose": [
            {
                "purpose": name,
                **_usage(
                    [item for item in events if item.get("purpose", "unscoped") == name],
                    truncated=truncated,
                ),
            }
            for name in purposes
        ],
        "scope": {"chat_ref": chat_ref, "session_ref": session_ref},
        "available": available,
        "truncated": truncated,
        "bytes_scanned": scanned,
        "skipped_records": skipped,
    }
