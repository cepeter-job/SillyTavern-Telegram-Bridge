"""Users control ending mode and confirm a finale without reopening completed canon."""

from dataclasses import replace

import pytest
from test_ending_state import ready
from test_memory_completion_safety import session_db as session_db
from test_narrative_arcs import arc, packet
from test_narrative_checkpoints import prepared
from test_narrative_reconciliation import add_story, run_reconciliation

from bridge.director_panels import director_panel
from bridge.director_room import director_room
from bridge.ending_service import load_ending_state
from bridge.narrative_settings import load_session_narrative_settings, save_session_narrative_settings


def test_ending_settings_are_session_owned_and_use_exact_revision(session_db):
    from bridge.ending_controls import configure_ending

    _, db, _ = session_db
    view = director_room(db, "chat", "s1")
    configure_ending(db, "chat", "s1", view["revision"], mode="closed_story", require_confirmation=True)
    pref = load_session_narrative_settings(db, "chat", "s1")
    assert pref.ending_mode == "closed_story" and pref.require_finale_confirmation is True
    assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    with pytest.raises(ValueError, match="changed"):
        configure_ending(db, "chat", "s1", view["revision"], mode="open_ended", require_confirmation=False)


@pytest.mark.parametrize(
    "mode,confirmation", [("auto", False), ("closed_story", "false"), (None, True), ("open_ended", 1)]
)
def test_ending_configuration_rejects_ambiguous_values(session_db, mode, confirmation):
    from bridge.ending_controls import configure_ending

    _, db, _ = session_db
    with pytest.raises(ValueError):
        configure_ending(
            db, "chat", "s1", director_room(db, "chat", "s1")["revision"], mode=mode, require_confirmation=confirmation
        )
    assert load_session_narrative_settings(db, "chat", "s1").ending_mode == "open_ended"


def confirming_story(case):
    _, db, _ = case
    prepared(case)
    pref = load_session_narrative_settings(db, "chat", "s1")
    save_session_narrative_settings(db, "chat", "s1", replace(pref, require_finale_confirmation=True))
    run_reconciliation(case, lambda *a, **k: packet(arc(), phase="escalation"))
    ready(db)
    return db


def test_finale_button_is_bound_to_current_readiness_and_not_a_model_call(session_db):
    from bridge.ending_controls import confirm_finale

    _, _, session = session_db
    db = confirming_story(session_db)
    text, markup = director_panel(db, "chat", session, "alice", page="ending")
    assert "Begin finale" in [button["text"] for row in markup["inline_keyboard"] for button in row]
    assert "confirmation" in text.lower()
    view = director_room(db, "chat", "s1")
    cp = confirm_finale(db, "chat", "s1", view["revision"], operation_id="confirmed-first")
    assert load_ending_state(db, "chat", "s1").checkpoint_id == cp.checkpoint_id
    with pytest.raises(ValueError):
        confirm_finale(db, "chat", "s1", view["revision"], operation_id="confirmed-second")
    assert db.execute("SELECT COUNT(*) FROM narrative_checkpoints WHERE kind='pre_finale'").fetchone()[0] == 1


def test_new_story_input_invalidates_an_older_finale_confirmation(session_db):
    from bridge.ending_controls import confirm_finale

    db = confirming_story(session_db)
    view = director_room(db, "chat", "s1")
    add_story(db, "Mara postpones the confrontation.")
    with pytest.raises(ValueError, match="changed"):
        confirm_finale(db, "chat", "s1", view["revision"], operation_id="stale-confirmation")
    assert load_ending_state(db, "chat", "s1").lifecycle == "open"


def test_frozen_ending_settings_do_not_modify_a_finale(session_db):
    from bridge.ending_controls import configure_ending, confirm_finale

    db = confirming_story(session_db)
    confirm_finale(db, "chat", "s1", director_room(db, "chat", "s1")["revision"], operation_id="enter")
    with pytest.raises(ValueError, match="locked"):
        configure_ending(
            db, "chat", "s1", director_room(db, "chat", "s1")["revision"], mode="open_ended", require_confirmation=False
        )


def test_setup_advanced_exposes_ending_mode_and_strict_confirmation_values():
    from bridge.narrative_panels import narrative_options
    from bridge.narrative_settings import change_narrative_setting, preset_narrative_settings

    assert ("ending_mode", "Story ending") in narrative_options("advanced")
    pref = change_narrative_setting(preset_narrative_settings("world_driven"), "ending_mode", "closed_story")
    pref = change_narrative_setting(pref, "require_finale_confirmation", "true")
    assert pref.require_finale_confirmation is True and pref.ending_mode == "closed_story"
    assert pref.scene_focus == "world"
    with pytest.raises(ValueError):
        change_narrative_setting(pref, "require_finale_confirmation", "sometimes")


def test_finale_generation_policy_reserves_epilogue_for_a_separate_call(session_db):
    from test_narrative_checkpoints import enter

    from bridge.narrative_context import narrative_context_for_session

    db = prepared(session_db)
    enter(db)
    context = narrative_context_for_session(db, "chat", "s1", "story")
    assert "finale" in context.lower() and "separate epilogue" in context.lower()
    assert "do not" in context.lower()
