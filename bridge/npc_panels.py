"""Telegram panels for the session-scoped NPC Bank."""

from __future__ import annotations

from typing import Any

from bridge.cards import send_panel_message
from bridge.npc_service import NpcService
from bridge.panel_utils import panel_label, panel_navigation, panel_page
from bridge.request_types import RequestContext

_FIELD_ORDER = (
    "role",
    "appearance",
    "voice",
    "background",
    "canon",
    "location",
    "agenda",
    "relationship",
    "mood",
    "status",
    "secrets",
)


def _render_value(value: Any) -> str:
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    return str(value)


def send_npc_menu(
    token: str,
    chat_id: str,
    db,
    session: dict[str, str],
    message_id: int | None = None,
    page: int = 0,
    *,
    npc_service: NpcService,
    request_context: RequestContext,
) -> None:
    entities = sorted(
        npc_service.list_npcs(db, chat_id, session["session_id"]),
        key=lambda item: item.display_name.casefold(),
    )
    options = [(entity.npc_id, entity.display_name) for entity in entities]
    page_options, current_page, total_pages = panel_page(options, page)
    rows = [
        [
            {
                "text": panel_label(label),
                "callback_data": f"npc:view:{npc_id}",
            }
        ]
        for npc_id, label in page_options
    ]
    navigation = panel_navigation("npc", current_page, total_pages)
    if navigation:
        rows.append(navigation)
    rows.append(
        [
            {"text": "🔄 Refresh", "callback_data": "npc:refresh"},
            {"text": "❌ Close", "callback_data": "npc:close"},
        ]
    )
    page_label = f" (page {current_page + 1}/{total_pages})" if total_pages > 1 else ""
    text = (
        f"NPC Bank{page_label}\nTracked supporting characters in this session."
        if entities
        else "NPC Bank\nNo supporting characters are tracked yet. Use Refresh after the story introduces one."
    )
    send_panel_message(
        token,
        chat_id,
        text,
        {"inline_keyboard": rows},
        message_id,
        request_context=request_context,
    )


def send_npc_detail(
    token: str,
    chat_id: str,
    db,
    session: dict[str, str],
    fields: dict[str, str],
    npc_id: int,
    message_id: int | None = None,
    *,
    npc_service: NpcService,
    request_context: RequestContext,
) -> None:
    result = npc_service.visible_fields(db, chat_id, session, fields, npc_id)
    if result is None:
        send_npc_menu(
            token,
            chat_id,
            db,
            session,
            message_id,
            npc_service=npc_service,
            request_context=request_context,
        )
        return
    entity, visible = result
    lines = [f"NPC: {entity.display_name}"]
    if entity.aliases:
        lines.append("Aliases: " + ", ".join(entity.aliases))
    for key in _FIELD_ORDER:
        state = visible.get(key)
        if state is None:
            continue
        rendered = _render_value(state.value).strip()
        if rendered:
            lines.append(f"{key.replace('_', ' ').title()}: {rendered}")
    if len(lines) == (2 if entity.aliases else 1):
        lines.append("No fields are visible to the active character.")
    rows = [
        [
            {"text": "📜 History", "callback_data": f"npc:history:{entity.npc_id}"},
            {"text": "⬅️ Back", "callback_data": "npc:menu"},
        ],
        [
            {"text": "🔄 Refresh", "callback_data": "npc:refresh"},
            {"text": "❌ Close", "callback_data": "npc:close"},
        ],
    ]
    send_panel_message(
        token,
        chat_id,
        "\n".join(lines),
        {"inline_keyboard": rows},
        message_id,
        request_context=request_context,
    )


def send_npc_history(
    token: str,
    chat_id: str,
    db,
    session: dict[str, str],
    fields: dict[str, str],
    npc_id: int,
    message_id: int | None = None,
    *,
    npc_service: NpcService,
    request_context: RequestContext,
) -> None:
    result = npc_service.visible_history(db, chat_id, session, fields, npc_id)
    if result is None:
        send_npc_menu(
            token,
            chat_id,
            db,
            session,
            message_id,
            npc_service=npc_service,
            request_context=request_context,
        )
        return
    entity, history = result
    lines = [f"NPC history — {entity.display_name}"]
    for change in reversed(history[-12:]):
        before = "∅" if change.before is None else _render_value(change.before.value)
        after = "∅" if change.after is None else _render_value(change.after.value)
        lines.append(f"row {change.source_rowid} · {change.field_key}: {before} → {after}")
    if not history:
        lines.append("No visible field history.")
    latest_changes = []
    seen = set()
    for change in reversed(history):
        if change.field_key not in seen:
            seen.add(change.field_key)
            latest_changes.append(change)
        if len(latest_changes) >= 6:
            break
    rows = [
        [
            {
                "text": f"↩️ {change.field_key.replace('_', ' ').title()}",
                "callback_data": (f"npc:undo:{entity.npc_id}:{change.field_key}:{change.change_id}"),
            }
        ]
        for change in latest_changes
    ]
    rows.append(
        [
            {"text": "⬅️ Back", "callback_data": f"npc:view:{entity.npc_id}"},
            {"text": "❌ Close", "callback_data": "npc:close"},
        ]
    )
    send_panel_message(
        token,
        chat_id,
        "\n".join(lines),
        {"inline_keyboard": rows},
        message_id,
        request_context=request_context,
    )


def send_npc_undo_confirm(
    token: str,
    chat_id: str,
    db,
    session: dict[str, str],
    npc_id: int,
    field_key: str,
    expected_change_id: int,
    message_id: int | None = None,
    *,
    npc_service: NpcService,
    request_context: RequestContext,
) -> None:
    entity = npc_service.get_npc(db, chat_id, session["session_id"], npc_id)
    if entity is None:
        send_npc_menu(
            token,
            chat_id,
            db,
            session,
            message_id,
            npc_service=npc_service,
            request_context=request_context,
        )
        return
    label = field_key.replace("_", " ").title()
    rows = [
        [
            {
                "text": "✅ Confirm undo",
                "callback_data": (f"npc:undo-confirm:{entity.npc_id}:{field_key}:{expected_change_id}"),
            },
            {"text": "❌ Cancel", "callback_data": f"npc:history:{entity.npc_id}"},
        ]
    ]
    send_panel_message(
        token,
        chat_id,
        f"Undo the latest {label} change for {entity.display_name}?",
        {"inline_keyboard": rows},
        message_id,
        request_context=request_context,
    )
