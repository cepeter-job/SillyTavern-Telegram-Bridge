"""Narrative Style labels and views shared by setup and existing-session controls."""

from __future__ import annotations

import json

from bridge.callback_tokens import dynamic_callback_token
from bridge.narrative_repository import narrative_settings_revision
from bridge.narrative_settings import NARRATIVE_EDIT_FIELDS, load_session_narrative_settings
from bridge.narrative_values import NarrativeSettings
from bridge.request_types import RequestContext
from bridge.telegram import send_panel_request

PRESET_LABELS = {
    "player_centric": "Player-centric",
    "ensemble": "Ensemble",
    "world_driven": "World-driven",
    "observer": "Observer",
    "custom": "Custom",
}
FIELD_LABELS = {
    "ending_mode": "Story ending",
    "require_finale_confirmation": "Finale confirmation",
    "pov_mode": "Point of view",
    "user_role": "Your role",
    "scene_focus": "Scene focus",
    "offscreen_policy": "Off-screen scenes",
    "user_control": "Control of your character",
}
FIELD_OPTIONS = {
    "ending_mode": (("open_ended", "Open-ended"), ("closed_story", "Closed Story")),
    "require_finale_confirmation": (("false", "Automatic when ready"), ("true", "Ask before finale")),
    "pov_mode": (
        ("first_person", "First-person, AI viewpoint"),
        ("third_person_user", "Third-person limited, user-anchored"),
        ("third_person_rotating", "Third-person limited, rotating"),
        ("omniscient", "Third-person omniscient"),
        ("cinematic", "Cinematic / objective"),
    ),
    "user_role": (
        ("protagonist", "Main protagonist"),
        ("major_cast", "Ensemble cast member"),
        ("peripheral", "May be peripheral"),
        ("observer", "Observer until you intervene"),
    ),
    "scene_focus": (("user", "Your experience"), ("ensemble", "Ensemble"), ("world", "World")),
    "offscreen_policy": (("rare", "Rare and brief"), ("bounded", "Bounded"), ("free", "Free, no forced return")),
    "user_control": (
        ("strict_reserved", "Strictly reserved"),
        ("physical_continuity", "Physical continuity"),
        ("contextual_continuity", "Routine contextual continuity"),
    ),
}


def narrative_options(view: str, field: str = "") -> list[tuple[str, str]]:
    if view == "presets":
        return [(key, label) for key, label in PRESET_LABELS.items() if key != "custom"]
    if view == "advanced":
        return [(key, FIELD_LABELS[key]) for key in NARRATIVE_EDIT_FIELDS]
    if view == "field" and field in NARRATIVE_EDIT_FIELDS:
        return list(FIELD_OPTIONS[field])
    raise ValueError("Choose an available Narrative Style view.")


def narrative_style_summary(settings: NarrativeSettings) -> str:
    values = settings.to_dict()
    lines = [f"Narrative: {PRESET_LABELS[settings.preset]}"]
    for name in NARRATIVE_EDIT_FIELDS:
        label = dict(FIELD_OPTIONS[name]).get(str(values[name]).lower(), str(values[name]))
        lines.append(f"{FIELD_LABELS[name]}: {label}")
    lines.append("Your dialogue, thoughts, commitments, and important decisions remain yours.")
    if settings.preset in {"world_driven", "observer"}:
        lines.append("Recommended: I am not MC (not enabled automatically).")
    return "\n".join(lines)


def send_narrative_menu(
    token: str,
    chat_id: str,
    session: dict,
    message_id: int | None = None,
    *,
    request_context: RequestContext,
    view: str = "presets",
    field: str = "",
) -> None:
    session_id = session["session_id"]
    settings = load_session_narrative_settings(request_context.db, chat_id, session_id)
    revision = narrative_settings_revision(request_context.db, chat_id, session_id)

    def button(label: str, action: str, value: str = "", selected_field: str = "") -> dict:
        encoded = json.dumps(
            {
                "session_id": session_id,
                "actor_id": request_context.actor_id,
                "settings_revision": revision,
                "action": action,
                "value": value,
                "field": selected_field,
            }
        )
        key = dynamic_callback_token("narrative", encoded, chat_id, db=request_context.db)
        return {"text": label, "callback_data": "narrative:" + key}

    options = narrative_options(view, field)
    if view == "presets":
        rows = [[button(label, "preset", key)] for key, label in options]
        rows.extend([[button("Advanced", "view", "advanced")], [button("Save as my default", "default")]])
    elif view == "advanced":
        rows = [[button(label, "view", "field", key)] for key, label in options]
    else:
        rows = [[button(label, "set", key, field)] for key, label in options]
    if view != "presets":
        rows.append([button("Back", "view", "advanced" if view == "field" else "presets")])
    rows.append([button("Close", "close")])
    text = "Narrative Style — current story\n\n" + narrative_style_summary(settings)
    if view == "field":
        text += f"\n\nChoose {FIELD_LABELS[field].lower()}."
    else:
        text += "\n\nChanges affect this story only. Save as my default prefills future character setup."
    payload: dict = {"chat_id": chat_id, "text": text, "reply_markup": {"inline_keyboard": rows}}
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
