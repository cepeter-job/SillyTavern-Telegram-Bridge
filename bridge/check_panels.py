"""Telegram panel for session-scoped automatic action checks."""

from __future__ import annotations

import sqlite3

from bridge.action_adjudication import action_mode
from bridge.cards import send_panel_message
from bridge.simulation_repository import list_checks
from bridge.simulation_values import text

_MODE_LABELS = {
    "auto": "Auto",
    "director": "Director",
    "manual": "Manual",
}


def _mode_button(mode: str, current: str) -> dict[str, str]:
    mark = "✅ " if mode == current else ""
    return {"text": mark + _MODE_LABELS[mode], "callback_data": f"checkmode:{mode}"}


def send_check_menu(
    token: str,
    chat_id: str,
    db: sqlite3.Connection,
    session_id: str,
    message_id: int | None = None,
    *,
    request_context,
) -> None:
    current = action_mode(db, chat_id, session_id)
    label = _MODE_LABELS[current]
    rows = [
        [_mode_button("auto", current), _mode_button("director", current), _mode_button("manual", current)],
        [
            {"text": "🎲 Manual Check", "callback_data": "checkmode:manual_help"},
            {"text": "📊 Recent Checks", "callback_data": "checkmode:recent"},
        ],
        [{"text": "❌ Close", "callback_data": "checkmode:close"}],
    ]
    send_panel_message(
        token,
        chat_id,
        (
            "🎲 Action Checks\n"
            f"Current mode: {label}\n\n"
            "Auto uses Utility to decide whether an in-world action needs a check. "
            "Director uses the session Director instead. Manual disables automatic adjudication.\n\n"
            "The bridge always owns dice and numeric modifiers."
        ),
        {"inline_keyboard": rows},
        message_id,
        request_context=request_context,
    )


def send_manual_check_help(
    token: str,
    chat_id: str,
    message_id: int,
    *,
    request_context,
) -> None:
    send_panel_message(
        token,
        chat_id,
        (
            "🎲 Manual Check\n\n"
            "Send: /check <domain> <DC> <action>\n"
            "Example: /check stealth 13 quietly open the door\n\n"
            "DC must be an integer from 1 to 20. The bridge records the roll for the next story turn."
        ),
        {
            "inline_keyboard": [
                [{"text": "⬅️ Back", "callback_data": "checkmode:back"}],
                [{"text": "❌ Close", "callback_data": "checkmode:close"}],
            ]
        },
        message_id,
        request_context=request_context,
    )


def send_recent_checks(
    token: str,
    chat_id: str,
    db: sqlite3.Connection,
    session_id: str,
    message_id: int,
    *,
    request_context,
) -> None:
    checks = list_checks(db, chat_id, session_id, limit=5)
    lines = ["🎲 Recent Checks"]
    if not checks:
        lines.extend(["", "No saved checks yet."])
    else:
        for check in checks:
            action = text(check["action"], 120)
            total = check["roll"] + check["modifier"]
            lines.extend(
                [
                    "",
                    f"{check['domain']}: {action}",
                    (
                        f"d20 {check['roll']} {check['modifier']:+d} = {total} vs DC {check['dc']} "
                        f"— {check['outcome'].replace('_', ' ')}"
                    ),
                ]
            )
    send_panel_message(
        token,
        chat_id,
        "\n".join(lines),
        {
            "inline_keyboard": [
                [{"text": "⬅️ Back", "callback_data": "checkmode:back"}],
                [{"text": "❌ Close", "callback_data": "checkmode:close"}],
            ]
        },
        message_id,
        request_context=request_context,
    )
