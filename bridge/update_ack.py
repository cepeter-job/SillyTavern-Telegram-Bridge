"""Durable one-shot acknowledgement for verified self-update restarts."""

from __future__ import annotations

import json
import logging
import os
import re
import stat
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from bridge.self_update import MARKER, UpdateRefused, version_tuple
from bridge.settings import AppSettings

_PENDING_ACK = "pending-update-ack.json"
_MAX_STATE_BYTES = 4096
_CHAT_ID = re.compile(r"-?[1-9][0-9]{0,19}\Z")

VersionBackend = Callable[..., str]
SendTextBackend = Callable[[str, str, str], Any]


def pending_update_ack_path(app_settings: AppSettings) -> Path:
    return app_settings.bridge_home / _PENDING_ACK


def _valid_identity(chat_id: str, version: str) -> bool:
    if not _CHAT_ID.fullmatch(chat_id):
        return False
    try:
        version_tuple(version)
    except UpdateRefused:
        return False
    return True


def _sync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def arm_pending_update_ack(chat_id: str, version: str, *, app_settings: AppSettings) -> None:
    """Persist the chat/version pair before the scheduled service restart fires."""
    chat_id = str(chat_id).strip()
    version = str(version).strip()
    if not _valid_identity(chat_id, version):
        raise ValueError("invalid pending update acknowledgement identity")

    path = pending_update_ack_path(app_settings)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = json.dumps(
        {"format": 1, "chat_id": chat_id, "version": version},
        separators=(",", ":"),
        sort_keys=True,
    ) + "\n"

    fd, temporary = tempfile.mkstemp(prefix=".pending-update-ack-", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        path.chmod(0o600)
        _sync_directory(path.parent)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _discard_invalid(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        logging.warning("Could not discard invalid pending update acknowledgement")


def _read_pending(path: Path) -> tuple[str, str] | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        logging.warning("Could not inspect pending update acknowledgement")
        return None

    if (
        not stat.S_ISREG(info.st_mode)
        or not 0 < info.st_size <= _MAX_STATE_BYTES
        or (os.name == "posix" and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077))
    ):
        _discard_invalid(path)
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        _discard_invalid(path)
        return None
    if not isinstance(payload, dict) or payload.get("format") != 1:
        _discard_invalid(path)
        return None

    chat_id = str(payload.get("chat_id") or "").strip()
    version = str(payload.get("version") or "").strip()
    if not _valid_identity(chat_id, version):
        _discard_invalid(path)
        return None
    return chat_id, version


def _deployment_version(*, app_settings: AppSettings) -> str:
    marker = app_settings.update_live_dir / MARKER
    try:
        if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > _MAX_STATE_BYTES:
            return "unknown"
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return "unknown"
    if not isinstance(payload, dict):
        return "unknown"
    version = str(payload.get("version") or "").strip()
    try:
        version_tuple(version)
    except UpdateRefused:
        return "unknown"
    return version


def acknowledge_pending_update(
    token: str,
    *,
    app_settings: AppSettings,
    send_text_backend: SendTextBackend,
    version_backend: VersionBackend = _deployment_version,
) -> bool:
    """Confirm an armed update only after the restarted process is healthy enough to send."""
    path = pending_update_ack_path(app_settings)
    pending = _read_pending(path)
    if pending is None:
        return False

    chat_id, expected_version = pending
    try:
        current_version = version_backend(app_settings=app_settings)
    except Exception:
        logging.warning("Could not verify the pending update acknowledgement version")
        return False
    if current_version != expected_version:
        logging.warning("Pending update acknowledgement version does not match the running deployment")
        return False

    try:
        send_text_backend(
            token,
            chat_id,
            f"✅ Update complete — Running v{expected_version} successfully.",
        )
    except Exception:
        logging.warning("Could not deliver post-restart update acknowledgement")
        return False

    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        logging.warning("Post-restart update acknowledgement was sent but its marker could not be cleared")
    return True
