"""Pure presentation data for the Director Goal panel."""

from __future__ import annotations


def director_goal_panel(goal: str) -> tuple[str, dict]:
    value = str(goal or "").strip()
    preview = value[:1200]
    if len(value) > len(preview):
        preview += "…\n\nOpen /director or the Mini App to review the full objective."
    text = "Director objective\n\n" + (preview or "No hidden objective is set.")
    markup = {
        "inline_keyboard": [
            [{"text": "✏️ Set objective", "callback_data": "goal:set"}],
            [
                {"text": "🧹 Clear", "callback_data": "goal:clear"},
                {"text": "❌ Close", "callback_data": "goal:close"},
            ],
        ]
    }
    return text, markup
