"""Bounded, revision-checked native World Info management with verified backups."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from bridge.card_content import active_world_files, encode_world_files, world_file_paths
from bridge.miniapp_auth import MiniAppIdentity
from bridge.miniapp_context import digest, require_confirmation, session_scope, text
from bridge.miniapp_errors import MiniAppError
from bridge.miniapp_types import ApiRoute
from bridge.sqlite_store import write_transaction
from bridge.world_management import delete_world_info_file
from bridge.world_storage import install_world_info_document

_WORLD_LOCK = threading.RLock()
_MAX_WORLD_BYTES = 1024 * 1024


def _world(services: Any, filename: str):
    path = next((p for p in world_file_paths(app_settings=services.config) if p.name == filename), None)
    if path is None or path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_WORLD_BYTES:
        raise MiniAppError("World not found or exceeds the 1 MB editor limit.", status=404)
    with path.open("rb") as stream:
        raw = stream.read(_MAX_WORLD_BYTES + 1)
    if len(raw) > _MAX_WORLD_BYTES:
        raise MiniAppError("World exceeds the editor limit.", status=413)
    return path, raw


def _document(values: dict) -> bytes:
    doc = values.get("document")
    if not isinstance(doc, dict) or not isinstance(doc.get("entries"), dict) or len(doc["entries"]) > 2000:
        raise MiniAppError("World JSON must contain an entries object, with at most 2000 entries.")
    try:
        raw = json.dumps(doc, ensure_ascii=False, allow_nan=False, indent=2).encode()
    except (ValueError, TypeError):
        raise MiniAppError("Invalid World JSON.") from None
    if len(raw) > _MAX_WORLD_BYTES:
        raise MiniAppError("World exceeds the 1 MB editor limit.", status=413)
    return raw


def _backup(services: Any, filename: str, raw: bytes) -> None:
    root = services.config.bridge_home / "backups/worlds"
    if root.is_symlink():
        raise MiniAppError("Unsafe World backup directory.")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Safe server-derived filename; O_EXCL prevents replacement of an existing backup.
    backup = root / (Path(filename).stem + "." + str(time.time_ns()) + ".json")
    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    if backup.read_bytes() != raw:
        raise MiniAppError("World backup verification failed.")


def worlds(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    with session_scope(services, who, values) as scope:
        active = active_world_files(scope.session.get("world_file", ""), app_settings=services.config)
        return {
            "worlds": [
                {"filename": p.name, "active": p.name in active} for p in world_file_paths(app_settings=services.config)
            ][:200],
            "session": scope.session,
        }


def world_info(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    path, raw = _world(services, text(values, "filename"))
    try:
        document = json.loads(raw)
    except (ValueError, UnicodeError):
        raise MiniAppError("World file is not valid JSON.") from None
    return {"filename": path.name, "document": document, "digest": hashlib.sha256(raw).hexdigest()}


def create_world(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    filename = text(values, "filename", 120)
    if (
        not filename.endswith(".json")
        or filename.startswith(".")
        or ".." in filename
        or not all(ch.isalnum() or ch in " _-." for ch in filename)
    ):
        raise MiniAppError("Use a simple JSON filename without paths or wildcards.")
    raw = _document(values)
    with session_scope(services, who, values, write=True), _WORLD_LOCK:
        if len(world_file_paths(app_settings=services.config)) >= 200:
            raise MiniAppError("World catalog limit reached.")
        install_world_info_document(filename, raw, app_settings=services.config)
        return {"created": True, "filename": filename}


def edit_world(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    require_confirmation(values)
    expected = digest(values)
    raw = _document(values)
    with session_scope(services, who, values, write=True), _WORLD_LOCK:
        path, original = _world(services, text(values, "filename"))
        if hashlib.sha256(original).hexdigest() != expected:
            raise MiniAppError("World changed. Refresh before applying edits.", status=409)
        _backup(services, path.name, original)
        fd, name = tempfile.mkstemp(prefix=".miniapp-world-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise MiniAppError("World changed during save.", status=409)
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)
        return {"saved": True}


def select_worlds(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    selected = values.get("worlds")
    if not isinstance(selected, list) or len(selected) > 50 or any(not isinstance(x, str) for x in selected):
        raise MiniAppError("Choose up to 50 World Info files.")
    with session_scope(services, who, values, write=True) as scope, _WORLD_LOCK:
        for filename in selected:
            _world(services, filename)
        services.session.update(
            scope.db,
            scope.chat_id,
            scope.session["session_id"],
            world_file=encode_world_files(list(dict.fromkeys(selected))),
        )
        return {"saved": True}


def delete_world(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    require_confirmation(values)
    with session_scope(services, who, values, write=True) as scope, _WORLD_LOCK:
        path, raw = _world(services, text(values, "filename"))
        if hashlib.sha256(raw).hexdigest() != digest(values):
            raise MiniAppError("World changed. Refresh before deleting.", status=409)
        _backup(services, path.name, raw)
        with write_transaction(scope.db):
            delete_world_info_file(scope.db, scope.chat_id, path.name, app_settings=services.config)
        return {"deleted": True}


def routes() -> list[ApiRoute]:
    return [
        ApiRoute("GET", "/worlds", worlds),
        ApiRoute("POST", "/worlds", create_world),
        ApiRoute("POST", "/worlds/select", select_worlds),
        ApiRoute("GET", "/worlds/{filename}", world_info),
        ApiRoute("PATCH", "/worlds/{filename}", edit_world),
        ApiRoute("DELETE", "/worlds/{filename}", delete_world),
    ]
