"""Private-session NPC Bank use cases for the Mini App."""

from __future__ import annotations

from typing import Any

from bridge.card_content import card_fields_from_file
from bridge.miniapp_auth import MiniAppIdentity
from bridge.miniapp_context import require_confirmation, session_scope, text
from bridge.miniapp_errors import MiniAppError
from bridge.miniapp_types import ApiRoute
from bridge.npc_extraction import refresh_npc_state_now
from bridge.npc_repository import list_npc_field_history


def _npc_id(values: dict) -> int:
    try:
        value = int(values.get("npc_id", ""))
    except (TypeError, ValueError):
        raise MiniAppError("Invalid NPC identifier.") from None
    if value <= 0:
        raise MiniAppError("Invalid NPC identifier.")
    return value


def _change_id(values: dict) -> int:
    try:
        value = int(values.get("change_id", ""))
    except (TypeError, ValueError):
        raise MiniAppError("Invalid NPC change identifier.") from None
    if value <= 0:
        raise MiniAppError("Invalid NPC change identifier.")
    return value


def _active_fields(services: Any, session: dict[str, str]) -> dict[str, str]:
    return card_fields_from_file(session["character_file"], app_settings=services.config)


def _field_payload(state, *, change_id: int | None = None) -> dict:
    payload = {
        "value": state.value,
        "mode": state.field_mode,
        "visibility": state.visibility,
        "updated_rowid": state.updated_rowid,
    }
    if change_id is not None:
        payload["change_id"] = int(change_id)
    return payload


def npcs(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    query = text(values, "q", 120, required=False).casefold()
    try:
        offset = max(0, int(values.get("offset", 0)))
    except (TypeError, ValueError):
        raise MiniAppError("Invalid page offset.") from None
    with session_scope(services, who, values) as scope:
        entities = services.npc.list_npcs(scope.db, scope.chat_id, scope.session["session_id"])
        filtered = [
            entity
            for entity in entities
            if not query
            or query in entity.display_name.casefold()
            or query in entity.canonical_name.casefold()
            or any(query in alias.casefold() for alias in entity.aliases)
        ]
        filtered.sort(key=lambda item: item.display_name.casefold())
        return {
            "npcs": [
                {
                    "npc_id": entity.npc_id,
                    "name": entity.display_name,
                    "aliases": list(entity.aliases),
                    "last_seen_rowid": entity.last_seen_rowid,
                }
                for entity in filtered[offset : offset + 24]
            ],
            "total": len(filtered),
            "offset": offset,
            "session": scope.session,
        }


def npc_detail(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    npc_id = _npc_id(values)
    with session_scope(services, who, values) as scope:
        fields = _active_fields(services, scope.session)
        visible = services.npc.visible_fields(scope.db, scope.chat_id, scope.session, fields, npc_id)
        if visible is None:
            raise MiniAppError("NPC not found.", status=404, code="not_found")
        entity, states = visible
        history = list_npc_field_history(scope.db, entity.npc_id)
        latest_change = {}
        for change in history:
            latest_change[change.field_key] = change.change_id
        return {
            "npc": {
                "npc_id": entity.npc_id,
                "name": entity.display_name,
                "aliases": list(entity.aliases),
                "first_seen_rowid": entity.first_seen_rowid,
                "last_seen_rowid": entity.last_seen_rowid,
            },
            "fields": {
                key: _field_payload(state, change_id=latest_change.get(key))
                for key, state in states.items()
            },
            "session": scope.session,
        }


def npc_history(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    npc_id = _npc_id(values)
    with session_scope(services, who, values) as scope:
        fields = _active_fields(services, scope.session)
        visible = services.npc.visible_history(scope.db, scope.chat_id, scope.session, fields, npc_id)
        if visible is None:
            raise MiniAppError("NPC not found.", status=404, code="not_found")
        entity, history = visible
        return {
            "npc": {"npc_id": entity.npc_id, "name": entity.display_name},
            "history": [
                {
                    "change_id": change.change_id,
                    "field": change.field_key,
                    "operation": change.operation,
                    "source_rowid": change.source_rowid,
                    "before": None if change.before is None else change.before.value,
                    "after": None if change.after is None else change.after.value,
                }
                for change in history[-40:]
            ],
            "session": scope.session,
        }


def undo_npc_field(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    require_confirmation(values)
    npc_id = _npc_id(values)
    expected_change = _change_id(values)
    field_key = text(values, "field", 32)
    with session_scope(services, who, values, write=True) as scope:
        active_fields = _active_fields(services, scope.session)
        visible = services.npc.visible_history(
            scope.db,
            scope.chat_id,
            scope.session,
            active_fields,
            npc_id,
        )
        if visible is None:
            raise MiniAppError("NPC not found.", status=404, code="not_found")
        entity, visible_history = visible
        all_changes = [
            change
            for change in list_npc_field_history(scope.db, entity.npc_id)
            if change.field_key == field_key
        ]
        if not all_changes:
            raise MiniAppError("No NPC change is available to undo.", status=404, code="not_found")
        latest = all_changes[-1]
        if latest.change_id != expected_change:
            raise MiniAppError("NPC state changed. Refresh before undoing.", status=409, code="stale")
        if not any(change.change_id == latest.change_id for change in visible_history):
            raise MiniAppError("That NPC field is not visible to the active character.", status=403, code="forbidden")
        restored = services.npc.undo_latest_field_change(
            scope.db,
            scope.chat_id,
            scope.session["session_id"],
            npc_id,
            field_key,
        )
        return {"restored": restored}


def refresh_npcs(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    with session_scope(services, who, values, write=True) as scope:
        fields = _active_fields(services, scope.session)
        updates = refresh_npc_state_now(
            scope.db,
            scope.chat_id,
            scope.session,
            fields,
            provider_port=services.provider,
            app_settings=services.config,
        )
        return {"updates": updates}


def routes() -> list[ApiRoute]:
    return [
        ApiRoute("GET", "/npcs", npcs),
        ApiRoute("GET", "/npcs/{npc_id}", npc_detail),
        ApiRoute("GET", "/npcs/{npc_id}/history", npc_history),
        ApiRoute("POST", "/npcs/{npc_id}/undo", undo_npc_field),
        ApiRoute("POST", "/npcs/refresh", refresh_npcs, "npc_refresh"),
    ]
