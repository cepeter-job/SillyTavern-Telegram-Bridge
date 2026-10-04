"""Validated narrative preferences and explicit session/default persistence."""

import json
import sqlite3
import time
from dataclasses import replace

from bridge.light_novel_repository import invalidate_choice_sets
from bridge.narrative_repository import (
    load_narrative_default_row,
    load_narrative_settings_row,
    narrative_settings_revision,
    store_narrative_default_row,
    store_narrative_settings_if_revision,
    store_narrative_settings_row,
)
from bridge.narrative_values import NarrativeSettings
from bridge.sqlite_store import write_transaction

_PRESETS = {
    "player_centric": NarrativeSettings(),
    "ensemble": NarrativeSettings(
        preset="ensemble",
        pov_mode="third_person_rotating",
        user_role="major_cast",
        scene_focus="ensemble",
        offscreen_policy="bounded",
    ),
    "world_driven": NarrativeSettings(
        preset="world_driven",
        pov_mode="third_person_rotating",
        user_role="peripheral",
        scene_focus="world",
        offscreen_policy="free",
    ),
    "observer": NarrativeSettings(
        preset="observer", pov_mode="cinematic", user_role="observer", scene_focus="world", offscreen_policy="free"
    ),
}

_CHOICES = {
    "pov_mode": {"first_person", "third_person_user", "third_person_rotating", "omniscient", "cinematic"},
    "user_role": {"protagonist", "major_cast", "peripheral", "observer"},
    "scene_focus": {"user", "ensemble", "world"},
    "offscreen_policy": {"rare", "bounded", "free"},
    "user_control": {"strict_reserved", "physical_continuity", "contextual_continuity"},
    "director_cadence_mode": {"adaptive", "fixed"},
    "ending_mode": {"open_ended", "closed_story"},
}


def preset_narrative_settings(preset: str) -> NarrativeSettings:
    if not isinstance(preset, str) or preset not in _PRESETS:
        raise ValueError("Choose Player-centric, Ensemble, World-driven, or Observer.")
    return _PRESETS[preset]


def normalize_narrative_settings(values: dict[str, object]) -> NarrativeSettings:
    if not isinstance(values, dict):
        raise ValueError("Narrative settings must be an object.")
    if values.keys() - NarrativeSettings().to_dict().keys():
        raise ValueError("Unknown narrative setting.")
    preset = values.get("preset", "player_centric")
    if not isinstance(preset, str):
        raise ValueError("Choose a valid narrative preset.")
    base = preset_narrative_settings("player_centric" if preset == "custom" else preset)
    merged = base.to_dict() | values
    text_values: dict[str, str] = {}
    for name, choices in _CHOICES.items():
        value = merged[name]
        if not isinstance(value, str) or value not in choices:
            raise ValueError(f"Invalid narrative setting: {name}.")
        text_values[name] = value
    interval = merged["director_fixed_interval"]
    if type(interval) is not int or not 1 <= interval <= 100:
        raise ValueError("Director interval must be an integer from 1 to 100 turns.")
    confirmation = merged["require_finale_confirmation"]
    if not isinstance(confirmation, bool):
        raise ValueError("Finale confirmation must be true or false.")
    if text_values["pov_mode"] == "first_person" and text_values["scene_focus"] == "user":
        raise ValueError("First-person narration requires an AI-controlled viewpoint, not the reserved user.")
    settings = NarrativeSettings(
        preset=base.preset,
        pov_mode=text_values["pov_mode"],
        user_role=text_values["user_role"],
        scene_focus=text_values["scene_focus"],
        offscreen_policy=text_values["offscreen_policy"],
        user_control=text_values["user_control"],
        director_cadence_mode=text_values["director_cadence_mode"],
        director_fixed_interval=interval,
        ending_mode=text_values["ending_mode"],
        require_finale_confirmation=confirmation,
    )
    return replace(settings, preset="custom") if preset == "custom" or settings != base else settings


def _decode_settings(raw: str | None) -> NarrativeSettings:
    if not raw or len(raw) > 4096:
        return NarrativeSettings()
    try:
        return normalize_narrative_settings(json.loads(raw))
    except (TypeError, ValueError, RecursionError):
        return NarrativeSettings()


def _encode_settings(settings: NarrativeSettings) -> str:
    if not isinstance(settings, NarrativeSettings):
        raise ValueError("Expected validated narrative settings.")
    normalized = normalize_narrative_settings(settings.to_dict())
    return json.dumps(normalized.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def load_user_narrative_default(db: sqlite3.Connection, owner_user_id: str) -> NarrativeSettings:
    return _decode_settings(load_narrative_default_row(db, owner_user_id))


def save_user_narrative_default(db: sqlite3.Connection, owner_user_id: str, settings: NarrativeSettings) -> None:
    if not isinstance(owner_user_id, str) or not owner_user_id.strip() or len(owner_user_id) > 100:
        raise ValueError("A valid authenticated owner is required.")
    payload = _encode_settings(settings)
    with write_transaction(db):
        store_narrative_default_row(db, owner_user_id, payload, time.time())


def load_session_narrative_settings(db: sqlite3.Connection, chat_id: str, session_id: str) -> NarrativeSettings:
    return _decode_settings(load_narrative_settings_row(db, chat_id, session_id))


def save_session_narrative_settings(
    db: sqlite3.Connection, chat_id: str, session_id: str, settings: NarrativeSettings
) -> None:
    payload = _encode_settings(settings)
    with write_transaction(db):
        store_narrative_settings_row(db, chat_id, session_id, payload, time.time())
        invalidate_choice_sets(db, chat_id, session_id)


NARRATIVE_EDIT_FIELDS = ("pov_mode", "user_role", "scene_focus", "offscreen_policy", "user_control")


def change_narrative_setting(settings: NarrativeSettings, field: str, value: str) -> NarrativeSettings:
    if field not in NARRATIVE_EDIT_FIELDS:
        raise ValueError("Choose an available Narrative Style field.")
    return normalize_narrative_settings(settings.to_dict() | {"preset": "custom", field: value})


def apply_narrative_preference(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    *,
    actor_id: str,
    expected_revision: int,
    action: str,
    value: str = "",
    field: str = "",
) -> NarrativeSettings:
    """Apply one current-panel action atomically; never inherit a personal default live."""
    if not actor_id or type(expected_revision) is not int or expected_revision < -1:
        raise ValueError("Reopen Narrative Style before making a change.")
    with write_transaction(db):
        if narrative_settings_revision(db, chat_id, session_id) != expected_revision:
            raise ValueError("Narrative Style changed. Reopen /narrative to use the latest settings.")
        current = load_session_narrative_settings(db, chat_id, session_id)
        if action == "default":
            save_user_narrative_default(db, actor_id, current)
            return current
        if action == "preset":
            changed = preset_narrative_settings(value)
        elif action == "set":
            changed = change_narrative_setting(current, field, value)
        else:
            raise ValueError("Choose an available Narrative Style action.")
        if not store_narrative_settings_if_revision(
            db, chat_id, session_id, _encode_settings(changed), expected_revision, time.time()
        ):
            raise ValueError("Narrative Style changed. Reopen /narrative to use the latest settings.")
        invalidate_choice_sets(db, chat_id, session_id)
        return changed


def initialize_session_narrative_settings(db: sqlite3.Connection, chat_id: str, session_id: str) -> None:
    """Pin an explicit new-session default without replacing existing preferences."""
    with write_transaction(db):
        if load_narrative_settings_row(db, chat_id, session_id) is None:
            save_session_narrative_settings(db, chat_id, session_id, NarrativeSettings())
