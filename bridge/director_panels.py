"""Bounded Telegram views of hidden plans, shared by commands and callbacks."""

from __future__ import annotations

import html
import json
import sqlite3
from typing import Any

from bridge.callback_tokens import dynamic_callback_token
from bridge.director_room import director_room
from bridge.request_types import RequestContext
from bridge.telegram import send_panel_request


def _escape(value: str, budget: int = 720) -> str:
    parts = []
    used = 0
    for char in str(value):
        escaped = html.escape(char)
        size = len(escaped.encode("utf-16-le")) // 2
        if used + size > budget:
            parts.append("…")
            break
        parts.append(escaped)
        used += size
    return "".join(parts)


def director_panel(
    db: sqlite3.Connection, chat_id: str, session: dict, actor_user_id: str, *, page: str = "main"
) -> tuple[str, dict]:
    view = director_room(db, chat_id, str(session["session_id"]))
    scene = view["scene"]

    def button(label: str, action: str, **extra: Any) -> dict:
        value = {
            "session_id": session["session_id"],
            "actor_id": actor_user_id,
            "revision": view["revision"],
            "action": action,
            **extra,
        }
        handle = dynamic_callback_token("director", json.dumps(value), chat_id, db=db)
        return {"text": label, "callback_data": "director:" + handle}

    rows = []
    if page == "history":
        lines = ["<b>Director history</b>", "Plans and reasons, not character knowledge."]
        for item in view["history"][:5]:
            lines.append(_escape(f"{item['source']} · {item['result']}\n{item['direction'] or item['reason']}", 500))
        if not view["history"]:
            lines.append("No Director decisions yet.")
        rows.append([button("Back", "open")])
        return "\n\n".join(lines), {"inline_keyboard": rows}
    if page == "cadence" and view["mutable"]:
        rows = [[button("Adaptive", "set_cadence", cadence="adaptive", interval=6)]]
        rows.extend(
            [
                [button(f"Every {interval} turns", "set_cadence", cadence="fixed", interval=interval)]
                for interval in (4, 6, 10)
            ]
        )
        rows.extend([[button("Custom interval", "edit", scope="cadence")], [button("Back", "open")]])
        return (
            "<b>Director cadence</b>\n\nAdaptive reacts to meaningful events and checks more often near a finale. "
            "Fixed intervals use 1–100 completed Story turns.",
            {"inline_keyboard": rows},
        )
    lines = [
        "<b>Director Room</b>",
        "Hidden plans are shown here only. They are not part of the story.",
        f"<b>Phase</b>: {_escape(view['phase'].replace('_', ' '), 80)}",
        f"<b>Scene</b>: {_escape(scene['scene_id'] or 'Not initialized', 160)}\n"
        f"<b>Viewpoint</b>: {_escape(scene['viewpoint'] or 'Not set', 300)}\n"
        f"<b>Thread</b>: {_escape(scene['thread_id'] or 'Not set', 160)}",
        f"<b>Current direction</b>\n{_escape(view['direction'] or 'No accepted direction yet.')}",
        f"<b>Persistent objective</b>\n{_escape(view['objective'] or 'No manual objective.')}",
        f"<b>Cadence</b>: {_escape(view['cadence'].replace('_', ' '), 80)}",
    ]
    if view["degraded"]:
        lines.append("Director planning is degraded. Your saved story is unchanged; ordinary roleplay can continue.")
    if view["mutable"]:
        rows.extend(
            [
                [button("Reassess now", "reassess")],
                [
                    button("Edit next scene", "edit", scope="next_scene"),
                    button("Edit persistent objective", "edit", scope="persistent"),
                ],
                [button("Choose thread", "threads"), button("Cadence", "cadence")],
            ]
        )
        if view["objective"]:
            rows.append([button("Clear persistent objective", "clear_objective")])
    else:
        lines.append("This story is closing or has ended. Its Director Room is read-only.")
    rows.append([button("Decision history", "history"), button("Narrative Style", "narrative")])
    rows.append([button("Refresh", "open"), button("Close", "close")])
    if page == "threads" and view["mutable"]:
        rows = [
            [button(str(item["title"])[:44], "thread", thread_id=item["thread_id"])]
            for item in view["threads"]
            if item["status"] != "resolved"
        ]
        rows.append([button("Back", "open")])
        lines = [
            "<b>Choose the next story thread</b>",
            "This changes the plan for the next scene, not committed events.",
        ]
    return "\n\n".join(lines), {"inline_keyboard": rows}


def send_director_menu(
    token: str,
    chat_id: str,
    session: dict,
    message_id: int | None = None,
    *,
    request_context: RequestContext,
    page: str = "main",
) -> None:
    text, markup = director_panel(request_context.db, chat_id, session, request_context.actor_id, page=page)
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text, "reply_markup": markup, "parse_mode": "HTML"}
    if message_id is not None:
        payload["message_id"] = message_id
    try:
        send_panel_request(
            token,
            "editMessageText" if message_id is not None else "sendMessage",
            payload,
            request_context=request_context,
        )
    except RuntimeError as exc:
        if "not modified" not in str(exc).casefold():
            raise
