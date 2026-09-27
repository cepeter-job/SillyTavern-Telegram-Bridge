"""Owned session and shared native-persona administration through canonical services."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from bridge.metadata import set_meta
from bridge.miniapp_auth import MiniAppIdentity
from bridge.miniapp_context import digest, require_confirmation, session_scope, text
from bridge.miniapp_errors import MiniAppError
from bridge.miniapp_types import ApiRoute


def sessions(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    query = text(values, "q", 120, required=False).casefold()
    with session_scope(services, who, values) as scope:
        items = services.session.list(scope.db, scope.chat_id)
        return {
            "sessions": [s for s in items if query in str(s.get("title", "")).casefold()][:200],
            "session": scope.session,
            "total": len(items),
        }


def create_session(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    title = text(values, "title", 120)
    key = text(values, "operation_id", 100)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", key):
        raise MiniAppError("Invalid operation identifier.")
    session_id = "mini-" + hashlib.sha256((who.user_id + ":" + key).encode()).hexdigest()[:24]
    with session_scope(services, who, values, write=True) as scope:
        existing = services.session.list(scope.db, scope.chat_id)
        if len(existing) >= 200:
            raise MiniAppError("Session limit reached. Remove an unused session first.")
        if any(item["session_id"] == session_id for item in existing):
            raise MiniAppError("This creation request was already applied. Refresh the session list.", status=409)
        created = services.session.create(
            scope.db, scope.chat_id, services.config.default_model, session_id=session_id, title=title
        )
        return {"session": created}


def _target(services: Any, scope: Any, values: dict) -> dict:
    session_id = text(values, "target_id", 200)
    try:
        return services.session.load(scope.db, scope.chat_id, session_id, services.config.default_model)
    except ValueError:
        raise MiniAppError("Session not found in your private chat.", status=404) from None


def edit_session(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    title = text(values, "title", 120)
    with session_scope(services, who, values, write=True) as scope:
        target = _target(services, scope, values)
        services.session.update(scope.db, scope.chat_id, target["session_id"], title=title)
        return {"saved": True}


def select_session(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    with session_scope(services, who, values, write=True) as scope:
        target = _target(services, scope, values)
        set_meta(scope.db, f"active_session:{scope.chat_id}", target["session_id"])
        return {"session": target}


def delete_session(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    require_confirmation(values)
    with session_scope(services, who, values, write=True) as scope:
        target = _target(services, scope, values)
        deleted, _reason = services.session.delete(
            scope.db, scope.chat_id, target["session_id"], scope.session["session_id"]
        )
        if not deleted:
            raise MiniAppError("Deletion refused: active session, pending jobs or memory cleanup failure.", status=409)
        return {"deleted": True}


def _persona(services: Any, persona_id: str) -> dict:
    item = services.persona.get(persona_id)
    if item is None:
        raise MiniAppError("Persona not found.", status=404)
    data = {
        "id": persona_id,
        "name": str(item.get("name") or persona_id),
        "description": str(item.get("description") or ""),
    }
    data["digest"] = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    return data


def personas(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    with session_scope(services, who, values) as scope:
        return {
            "personas": [_persona(services, key) for key in list(services.persona.list())[:200]],
            "session": scope.session,
        }


def create_persona(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    with session_scope(services, who, values, write=True) as scope:
        if len(services.persona.list()) >= 200:
            raise MiniAppError("Persona limit reached.")
        persona_id = services.persona.create_and_select(
            scope.db,
            scope.chat_id,
            scope.session["session_id"],
            text(values, "logical_id", 64),
            text(values, "name", 120),
            text(values, "description", 4000),
        )
        return {"id": persona_id}


def edit_persona(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    with session_scope(services, who, values, write=True), services.persona.persona_edit_lock():
        persona_id = text(values, "persona_id", 255)
        if _persona(services, persona_id)["digest"] != digest(values):
            raise MiniAppError("Persona changed. Refresh before editing.", status=409)
        services.persona.update(persona_id, text(values, "name", 120), text(values, "description", 4000))
        return {"saved": True}


def select_persona(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    with session_scope(services, who, values, write=True) as scope:
        persona_id = text(values, "persona_id", 255, required=False)
        if not persona_id:
            services.persona.disable(scope.db, scope.chat_id, scope.session["session_id"])
        elif not services.persona.select(scope.db, scope.chat_id, scope.session["session_id"], persona_id):
            raise MiniAppError("Persona is unavailable.", status=404)
        return {"saved": True}


def delete_persona(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    require_confirmation(values)
    with session_scope(services, who, values, write=True) as scope, services.persona.persona_edit_lock():
        persona_id = text(values, "persona_id", 255)
        if _persona(services, persona_id)["digest"] != digest(values):
            raise MiniAppError("Persona changed. Refresh before deleting.", status=409)
        return {"deleted": services.persona.delete_if_unused(scope.db, persona_id)}


def routes() -> list[ApiRoute]:
    return [
        ApiRoute("GET", "/sessions", sessions),
        ApiRoute("POST", "/sessions", create_session),
        ApiRoute("PATCH", "/sessions/{target_id}", edit_session),
        ApiRoute("POST", "/sessions/{target_id}/select", select_session),
        ApiRoute("POST", "/sessions/{target_id}/delete", delete_session, "session_delete"),
        ApiRoute("GET", "/personas", personas),
        ApiRoute("POST", "/personas", create_persona),
        ApiRoute("PATCH", "/personas/{persona_id}", edit_persona),
        ApiRoute("POST", "/personas/select", select_persona),
        ApiRoute("DELETE", "/personas/{persona_id}", delete_persona),
    ]
