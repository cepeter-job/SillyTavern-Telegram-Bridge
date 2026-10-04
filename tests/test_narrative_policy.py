"""Narrative presets, preference isolation and agency-policy contracts."""

import json
import sqlite3
from contextlib import closing

import pytest

from bridge.migrations import run_migrations
from bridge.narrative_values import NarrativeSettings, NarrativeState
from bridge.schema import SCHEMA_MIGRATIONS


@pytest.fixture
def db():
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        run_migrations(connection, tuple(m for m in SCHEMA_MIGRATIONS if m.version < 10))
        connection.execute(
            "INSERT INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,world_file,"
            "created_at,updated_at) VALUES('chat','story','Story','','model','','',1,1)"
        )
        connection.commit()
        run_migrations(connection, SCHEMA_MIGRATIONS)
        connection.commit()
        yield connection


@pytest.mark.parametrize(
    ("preset", "pov", "role", "focus", "offscreen"),
    [
        ("player_centric", "third_person_user", "protagonist", "user", "rare"),
        ("ensemble", "third_person_rotating", "major_cast", "ensemble", "bounded"),
        ("world_driven", "third_person_rotating", "peripheral", "world", "free"),
        ("observer", "cinematic", "observer", "world", "free"),
    ],
)
def test_presets_pin_all_effective_controls(preset, pov, role, focus, offscreen):
    from bridge.narrative_settings import normalize_narrative_settings, preset_narrative_settings

    settings = preset_narrative_settings(preset)
    assert settings == NarrativeSettings(
        preset=preset, pov_mode=pov, user_role=role, scene_focus=focus, offscreen_policy=offscreen
    )
    assert settings.user_control == "physical_continuity"
    assert settings.director_cadence_mode == "adaptive"
    assert settings.director_fixed_interval == 6
    assert settings.ending_mode == "open_ended"
    assert settings.require_finale_confirmation is False
    assert normalize_narrative_settings(settings.to_dict()) == settings


def test_advanced_override_is_custom_and_reselecting_preset_restores_defaults():
    from bridge.narrative_settings import normalize_narrative_settings, preset_narrative_settings

    values = preset_narrative_settings("world_driven").to_dict()
    values["offscreen_policy"] = "bounded"
    changed = normalize_narrative_settings(values)
    assert changed.preset == "custom"
    assert changed.offscreen_policy == "bounded"
    assert changed.pov_mode == "third_person_rotating"
    assert preset_narrative_settings("world_driven").offscreen_policy == "free"
    assert normalize_narrative_settings({"preset": "custom"}).preset == "custom"
    assert normalize_narrative_settings({"preset": "ensemble", "director_fixed_interval": 4}).preset == "custom"


@pytest.mark.parametrize(
    "values",
    [
        {"preset": "invalid"},
        {"pov_mode": "invalid"},
        {"user_role": "invalid"},
        {"scene_focus": "invalid"},
        {"offscreen_policy": "invalid"},
        {"user_control": "full_autonomy"},
        {"director_cadence_mode": "invalid"},
        {"director_fixed_interval": 0},
        {"director_fixed_interval": 101},
        {"director_fixed_interval": True},
        {"director_fixed_interval": "6"},
        {"require_finale_confirmation": "false"},
        {"ending_mode": "invalid"},
        {"unexpected": "value"},
        {"pov_mode": "first_person", "scene_focus": "user"},
    ],
)
def test_invalid_settings_are_rejected(values):
    from bridge.narrative_settings import normalize_narrative_settings

    with pytest.raises(ValueError):
        normalize_narrative_settings(values)


def test_personal_default_is_only_a_prefill_and_session_is_independent(db):
    from bridge.narrative_settings import (
        load_session_narrative_settings,
        load_user_narrative_default,
        preset_narrative_settings,
        save_session_narrative_settings,
        save_user_narrative_default,
    )

    world = preset_narrative_settings("world_driven")
    observer = preset_narrative_settings("observer")
    save_user_narrative_default(db, "owner", world)
    assert load_user_narrative_default(db, "owner") == world
    assert load_user_narrative_default(db, "other") == NarrativeSettings()
    assert load_session_narrative_settings(db, "chat", "story") == NarrativeSettings()
    assert load_session_narrative_settings(db, "chat", "missing") == NarrativeSettings()
    save_session_narrative_settings(db, "chat", "story", observer)
    save_user_narrative_default(db, "owner", preset_narrative_settings("ensemble"))
    assert load_session_narrative_settings(db, "chat", "story") == observer
    assert load_session_narrative_settings(db, "other", "story") == NarrativeSettings()


def test_preferences_are_pure_on_read_and_writes_roll_back_with_caller(db):
    from bridge.narrative_settings import (
        load_session_narrative_settings,
        load_user_narrative_default,
        preset_narrative_settings,
        save_session_narrative_settings,
        save_user_narrative_default,
    )

    traced = []
    db.set_trace_callback(traced.append)
    load_session_narrative_settings(db, "chat", "story")
    load_user_narrative_default(db, "owner")
    assert not db.in_transaction
    assert not any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for sql in traced)
    db.execute("BEGIN")
    save_user_narrative_default(db, "owner", preset_narrative_settings("world_driven"))
    save_session_narrative_settings(db, "chat", "story", preset_narrative_settings("observer"))
    assert db.in_transaction
    db.rollback()
    assert load_user_narrative_default(db, "owner") == NarrativeSettings()
    assert load_session_narrative_settings(db, "chat", "story") == NarrativeSettings()
    assert not any("grounded_user" in sql for sql in traced)


def test_corrupt_stored_preferences_fall_back_without_repairing_storage(db):
    from bridge.narrative_settings import load_session_narrative_settings, load_user_narrative_default

    db.execute("UPDATE narrative_settings SET settings_json=?", (json.dumps({"pov_mode": "broken"}),))
    db.execute("INSERT INTO narrative_defaults VALUES('owner','[]',0)")
    db.commit()
    before = db.total_changes
    assert load_session_narrative_settings(db, "chat", "story") == NarrativeSettings()
    assert load_user_narrative_default(db, "owner") == NarrativeSettings()
    assert db.total_changes == before


def test_invalid_save_cannot_mutate_configuration_or_create_sessions(db):
    from bridge.narrative_settings import save_session_narrative_settings, save_user_narrative_default

    with pytest.raises(ValueError):
        save_user_narrative_default(db, "", NarrativeSettings())
    with pytest.raises(ValueError):
        save_session_narrative_settings(db, "chat", "story", NarrativeSettings(user_control="unsafe"))
    with pytest.raises(sqlite3.IntegrityError):
        save_session_narrative_settings(db, "chat", "missing", NarrativeSettings())
    assert not db.in_transaction
    assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


def test_world_policy_allows_free_cutaways_without_stealing_agency():
    from bridge.narrative_policy import narrative_policy, story_policy_text
    from bridge.narrative_settings import preset_narrative_settings

    policy = narrative_policy(preset_narrative_settings("world_driven"))
    text = story_policy_text(policy)
    assert "no forced return to the user" in text
    assert "one viewpoint per scene" in text
    assert "already-established user decision" in text
    assert "Never invent the user's dialogue, thoughts, intentions, emotional conclusions, commitments" in text
    assert policy.recommends_grounded_user is True
    assert narrative_policy(NarrativeSettings()).recommends_grounded_user is False


def test_user_limited_and_omniscient_pov_keep_user_interiority_reserved():
    from bridge.narrative_policy import narrative_policy, story_policy_text
    from bridge.narrative_settings import normalize_narrative_settings

    for pov in ("third_person_user", "omniscient"):
        text = story_policy_text(narrative_policy(normalize_narrative_settings({"pov_mode": pov})))
        assert "user's private thoughts remain reserved" in text


def test_observer_and_offscreen_choice_policies_do_not_recenter_user():
    from bridge.narrative_policy import choice_policy_text, group_policy_text, narrative_policy, story_policy_text
    from bridge.narrative_settings import preset_narrative_settings

    policy = narrative_policy(preset_narrative_settings("observer"))
    state = NarrativeState(user_present=False, viewpoint_character="Mara")
    assert "observable action and dialogue only" in story_policy_text(policy, state)
    choices = choice_policy_text(policy, state)
    assert "narrative steering" in choices
    assert "do not fabricate off-screen user actions" in choices
    assert "not to recenter the user" in group_policy_text(policy, state)


def test_continuity_modes_are_distinct_without_granting_decision_authority():
    from bridge.narrative_policy import narrative_policy, story_policy_text
    from bridge.narrative_settings import normalize_narrative_settings

    for control, expected in (
        ("strict_reserved", "Do not infer any new user movement"),
        ("physical_continuity", "already-established user decision"),
        ("contextual_continuity", "strongly implied by the user's preceding message"),
    ):
        text = story_policy_text(narrative_policy(normalize_narrative_settings({"user_control": control})))
        assert expected in text
        assert "Never invent the user's dialogue" in text
