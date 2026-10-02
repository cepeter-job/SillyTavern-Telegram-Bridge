"""Session summary panel owner."""

from __future__ import annotations

from bridge.cards import send_panel_message
from bridge.memory import get_session_summary


def send_summary_menu(token, chat_id, db, session, message_id=None, *, request_context):
    summary, covered = get_session_summary(db, chat_id, session["session_id"])
    state = f"Existing summary: {len(summary)} chars" if summary else "No summary exists yet."
    text = (
        "Session summary\n\n"
        + state
        + (
            "\nCovered through message row: "
            f"""{covered or "none"}"""
            "\n\nRegenerating uses the utility model and may take a while."
        )
    )
    markup = {
        "inline_keyboard": [
            [{"text": "✅ Regenerate summary", "callback_data": "summary:confirm"}],
            [{"text": "❌ Cancel", "callback_data": "summary:cancel"}],
        ]
    }
    send_panel_message(token, chat_id, text, markup, message_id, request_context=request_context)
