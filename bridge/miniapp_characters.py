"""Character management use cases reusing canonical native-card/proposal owners."""

from __future__ import annotations

import base64
import binascii
import hashlib
from typing import Any

from bridge.card_content import active_world_files, card_fields, character_card_paths, parse_png_chara_bytes
from bridge.character_optimizer import prepare_character_optimization
from bridge.character_proposals import stage_character_proposal
from bridge.character_quality import character_rank
from bridge.conversation_setup import ConversationSetupService
from bridge.limits import RAG_MAX_FILE_BYTES
from bridge.miniapp_auth import MiniAppIdentity
from bridge.miniapp_context import digest, require_confirmation, session_scope, text
from bridge.miniapp_errors import MiniAppError
from bridge.miniapp_types import ApiRoute, BinaryResult
from bridge.native_imports import (
    _CHARACTER_WRITE_LOCK,
    _character_target,
    _current_digest,
    _install_character_bytes,
    apply_character_proposal,
    character_delete_references,
    verify_character_card_backup,
)
from bridge.provider_errors import ProviderRequestError
from bridge.sqlite_store import write_transaction


def _card(services: Any, filename: str):
    # Resolve a server-enumerated path, not a client-provided filesystem path.
    path = next((p for p in character_card_paths(app_settings=services.config) if p.name == filename), None)
    if path is None or path.is_symlink() or not path.is_file() or path.stat().st_size > RAG_MAX_FILE_BYTES:
        raise MiniAppError("Character not found or too large.", status=404, code="not_found")
    with path.open("rb") as stream:
        raw = stream.read(RAG_MAX_FILE_BYTES + 1)
    if len(raw) > RAG_MAX_FILE_BYTES:
        raise MiniAppError("Character exceeds the file size limit.")
    return path, raw


def list_characters(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    query = text(values, "q", 120, required=False).casefold()
    try:
        offset = max(0, int(values.get("offset", 0)))
    except (ValueError, TypeError):
        raise MiniAppError("Invalid page offset.") from None
    paths = [p for p in character_card_paths(app_settings=services.config) if query in p.name.casefold()]
    rows = []
    with session_scope(services, who, values) as scope:
        for path in paths[offset : offset + 24]:
            try:
                _, raw = _card(services, path.name)
                fields = card_fields(parse_png_chara_bytes(raw), app_settings=services.config)
                rows.append(
                    {
                        "filename": path.name,
                        "name": fields.get("name", path.stem),
                        "rank": character_rank(scope.db, path.name, app_settings=services.config),
                        "active": path.name == scope.session["character_file"],
                        "digest": hashlib.sha256(raw).hexdigest(),
                    }
                )
            except (ValueError, OSError):
                rows.append({"filename": path.name, "name": path.stem, "unavailable": True, "rank": ""})
        return {"characters": rows, "total": len(paths), "offset": offset, "session": scope.session}


def character_info(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    path, raw = _card(services, text(values, "filename"))
    fields = card_fields(parse_png_chara_bytes(raw), app_settings=services.config)
    return {"filename": path.name, "fields": fields, "digest": hashlib.sha256(raw).hexdigest()}


def character_portrait(services: Any, who: MiniAppIdentity, values: dict) -> BinaryResult:
    _, raw = _card(services, text(values, "filename"))
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise MiniAppError("This character does not have a PNG portrait.")
    return BinaryResult(raw, "image/png")


def select_character(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    require_confirmation(values)
    filename = text(values, "filename")
    with session_scope(services, who, values, write=True) as scope, _CHARACTER_WRITE_LOCK:
        path, raw = _card(services, filename)
        name = card_fields(parse_png_chara_bytes(raw), app_settings=services.config)["name"]
        setup = ConversationSetupService(services.config, services.persona)
        state = setup.begin(scope.db, scope.chat_id, scope.session, who.user_id, path.name)
        nonce = state["nonce"]

        def choose(stage, action, value=""):
            return setup.choose(scope.db, scope.chat_id, scope.session, who.user_id, nonce, stage, action, value)

        mode = text({"mode": values.get("mode", "normal")}, "mode", 16)
        choose("mode", "pick", mode)
        if mode == "lightnovel":
            choose("strategy", "pick", text({"strategy": values.get("strategy", "a")}, "strategy", 1))
        persona = scope.session.get("persona_id", "")
        choose("persona", "pick", persona if services.persona.get(persona) else "")
        for world in active_world_files(scope.session.get("world_file", ""), app_settings=services.config):
            choose("world", "pick", world)
        choose("world", "next")
        choose("system_prompt", "pick", "")
        choose("session", "pick", "$new")
        setup.set_title(
            scope.db, scope.chat_id, scope.session, who.user_id, str(values.get("title") or name + " chat")[:120]
        )
        session = setup.apply(scope.db, scope.chat_id, scope.session, who.user_id, nonce)
        return {"session": session, "message": "New session selected. Use /start in Telegram to begin."}


def delete_character(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    require_confirmation(values)
    expected = digest(values)
    with session_scope(services, who, values, write=True) as scope, _CHARACTER_WRITE_LOCK:
        path, raw = _card(services, text(values, "filename"))
        if (
            path.name in {scope.session["character_file"], services.config.default_character_file}
            or path.resolve() == services.config.card_file.resolve()
        ):
            raise MiniAppError("Active or default characters cannot be deleted.", status=409)
        if hashlib.sha256(raw).hexdigest() != expected:
            raise MiniAppError("Character changed. Refresh before deleting.", status=409)
        verify_character_card_backup(path, raw, app_settings=services.config)
        with write_transaction(scope.db):
            if character_delete_references(scope.db, path.name) or _current_digest(path) != expected:
                raise MiniAppError("This character is referenced by a session or changed.", status=409)
            path.unlink()
        return {"deleted": True}


def upload_character(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    name = text(values, "filename")
    if (
        not name.endswith(".png")
        or name.startswith(".")
        or len(name.encode()) > 128
        or not all(ch.isalnum() or ch in " _-." for ch in name)
        or ".." in name
    ):
        raise MiniAppError("Use a simple PNG filename without path or wildcard characters.")
    data = text(values, "data", (RAG_MAX_FILE_BYTES + 2) // 3 * 4)
    try:
        raw = base64.b64decode(data, validate=True)
    except (ValueError, binascii.Error):
        raise MiniAppError("Invalid file encoding.") from None
    if len(raw) > RAG_MAX_FILE_BYTES:
        raise MiniAppError("Character card is too large.", status=413)
    with session_scope(services, who, values, write=True) as scope, _CHARACTER_WRITE_LOCK:
        fields = card_fields(parse_png_chara_bytes(raw), app_settings=services.config)
        target = _character_target(name, app_settings=services.config)
        expected = _current_digest(target)
        if not expected:
            _install_character_bytes(name, raw, "", app_settings=services.config)
            return {"installed": True, "filename": name, "fields": fields}
        nonce = stage_character_proposal(
            scope.db, scope.chat_id, "upload", name, raw, expected, request_context=scope.context
        )
        return {
            "installed": False,
            "filename": name,
            "nonce": nonce,
            "fields": fields,
            "original": card_fields(parse_png_chara_bytes(target.read_bytes()), app_settings=services.config),
            "kind": "upload",
        }


def optimize_character(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    expected = digest(values)
    with session_scope(services, who, values, write=True) as scope:
        path, raw = _card(services, text(values, "filename"))
        original = card_fields(parse_png_chara_bytes(raw), app_settings=services.config)
        try:
            draft = prepare_character_optimization(
                scope.db,
                scope.chat_id,
                scope.session,
                path.name,
                provider_port=services.provider,
                request_context=scope.context,
                suggestion=text(values, "suggestion", 2000, required=False),
                expected_digest=expected,
            )
        except ProviderRequestError as exc:
            raise MiniAppError(str(exc), status=exc.miniapp_status, code=exc.miniapp_code) from None
        return {
            "filename": draft.filename,
            "nonce": draft.nonce,
            "fields": draft.fields,
            "original": original,
            "digest": expected,
            "kind": "optimize",
        }


def apply_proposal(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    require_confirmation(values)
    with session_scope(services, who, values, write=True) as scope:
        message, filename = apply_character_proposal(
            scope.db,
            scope.chat_id,
            text(values, "nonce", 24),
            text(values, "action", 20),
            request_context=scope.context,
        )
        return {"message": message, "filename": filename}


def discard_proposal(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    from bridge.character_proposals import discard_character_proposal

    with session_scope(services, who, values, write=True) as scope:
        discard_character_proposal(scope.db, scope.chat_id, text(values, "nonce", 24), request_context=scope.context)
        return {"discarded": True}


def routes() -> list[ApiRoute]:
    return [
        ApiRoute("GET", "/characters", list_characters),
        ApiRoute("GET", "/characters/{filename}/portrait", character_portrait),
        ApiRoute("GET", "/characters/{filename}", character_info),
        ApiRoute("POST", "/characters/{filename}/select", select_character),
        ApiRoute("DELETE", "/characters/{filename}", delete_character),
        ApiRoute("POST", "/characters", upload_character),
        ApiRoute("POST", "/characters/{filename}/optimize", optimize_character, "character_optimize"),
        ApiRoute("POST", "/character-proposals/{nonce}/apply", apply_proposal),
        ApiRoute("POST", "/character-proposals/{nonce}/discard", discard_proposal),
    ]
