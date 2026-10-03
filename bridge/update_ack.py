"""Durable, revision-bound post-restart notification, attempted only after polling resumes."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import stat
import tempfile
import time
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any

from bridge.runtime_health import capture_deployment
from bridge.self_update import UpdateRefused, version_tuple
from bridge.settings import AppSettings
from bridge.topic_scope import parse_topic_scope, topic_scope_id

VersionBackend = Callable[..., str]
SendTextBackend = Callable[[str, str, str], Any]


class UpdateAckStatus(str, Enum):
    ABSENT = "absent"
    DELIVERED = "delivered"
    REVISION_MISMATCH = "revision_mismatch"
    RETRYABLE_ERROR = "retryable_error"


def pending_update_ack_path(app_settings: AppSettings) -> Path:
    return app_settings.bridge_home / "pending-update-ack.json"


def _valid(chat_id: str, version: str, commit: str) -> bool:
    base_chat, thread = parse_topic_scope(chat_id)
    if thread is not None and (not 0 < thread < 2**31 or topic_scope_id(base_chat, thread) != chat_id):
        return False
    if not re.fullmatch(r"-?[1-9][0-9]{0,19}", base_chat) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        return False
    try:
        version_tuple(version)
        return True
    except UpdateRefused:
        return False


def _sync_parent(path: Path) -> None:
    if os.name == "posix":
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def arm_pending_update_ack(chat_id: str, version: str, *, commit: str, app_settings: AppSettings) -> None:
    if not _valid(chat_id, version, commit):
        raise ValueError("Invalid update acknowledgement identity")
    path = pending_update_ack_path(app_settings)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.parent.stat()
    if path.parent.is_symlink() or (os.name == "posix" and (info.st_uid != os.geteuid() or info.st_mode & 0o022)):
        raise ValueError("Unsafe acknowledgement directory")
    payload = {"format": 2, "chat_id": chat_id, "version": version, "commit": commit, "created_at": time.time()}
    fd, name = tempfile.mkstemp(prefix=".update-ack-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        _sync_parent(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


def _discard(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
        _sync_parent(path.parent)
    except OSError:
        logging.warning("Could not clear update acknowledgement state")


def _read(path: Path) -> dict | None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return None
    except OSError:
        _discard(path)
        return None
    try:
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or not 0 < info.st_size <= 4096
                or (os.name == "posix" and (info.st_uid != os.geteuid() or info.st_mode & 0o077))
            ):
                raise ValueError("unsafe state")
            payload = json.loads(stream.read(4097))
        if not isinstance(payload, dict) or type(payload.get("format")) is not int or payload["format"] != 2:
            raise ValueError("invalid state")
        chat, version, commit = (payload.get(key) for key in ("chat_id", "version", "commit"))
        created = payload.get("created_at")
        if not all(isinstance(x, str) for x in (chat, version, commit)) or not _valid(chat, version, commit):
            raise ValueError("invalid state")
        if type(created) not in {int, float} or not math.isfinite(created) or not -30 <= time.time() - created <= 86400:
            raise ValueError("expired state")
        return payload
    except (OSError, ValueError, TypeError, UnicodeError):
        _discard(path)
        return None


def attempt_pending_update_ack(
    token: str,
    *,
    app_settings: AppSettings,
    send_text_backend: SendTextBackend,
    version_backend: VersionBackend | None = None,
    commit_backend: VersionBackend | None = None,
) -> UpdateAckStatus:
    path = pending_update_ack_path(app_settings)
    pending = _read(path)
    if pending is None:
        return UpdateAckStatus.ABSENT
    try:
        current = capture_deployment(app_settings) if version_backend is None or commit_backend is None else None
        version = version_backend(app_settings=app_settings) if version_backend else current.version
        commit = commit_backend(app_settings=app_settings) if commit_backend else current.commit
    except Exception:
        logging.warning("Post-restart update acknowledgement identity could not be resolved")
        return UpdateAckStatus.RETRYABLE_ERROR
    if version != pending["version"] or commit != pending["commit"]:
        logging.warning("Update acknowledgement does not match the loaded process revision")
        return UpdateAckStatus.REVISION_MISMATCH
    try:
        send_text_backend(token, pending["chat_id"], f"✅ Update complete — Running v{version} successfully.")
    except Exception:
        logging.warning("Post-restart update acknowledgement could not be delivered")
        return UpdateAckStatus.RETRYABLE_ERROR
    _discard(path)
    return UpdateAckStatus.DELIVERED
