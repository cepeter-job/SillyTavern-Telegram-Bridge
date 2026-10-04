"""Ending goals and readiness are durable, revision-bound and never model-authored facts."""

from dataclasses import asdict

import pytest
from test_memory_completion_safety import session_db as session_db
from test_narrative_arcs import arc, packet
from test_narrative_reconciliation import add_story, run_reconciliation

from bridge.ending_repository import load_ending_row, save_ending_row
from bridge.ending_service import ending_goal_history, load_ending_state, mark_finale_ready, set_ending_goal
from bridge.ending_values import validate_ending_transition
from bridge.narrative_repository import load_narrative_clock
from bridge.narrative_settings import preset_narrative_settings, save_session_narrative_settings
from bridge.sqlite_store import write_transaction


def goal(db, text, *, source="user", reason="", **versions):
    state = load_ending_state(db, "chat", "s1")
    params = {
        "story_revision": load_narrative_clock(db, "chat", "s1")["state_revision"],
        "expected_lifecycle_revision": state.lifecycle_revision,
        "expected_goal_revision": state.goal_revision,
    }
    params.update(versions)
    return set_ending_goal(db, "chat", "s1", text, source=source, reason=reason, **params)


def ready_case(session_db):
    _, db, _ = session_db
    style = preset_narrative_settings("world_driven")
    from bridge.narrative_settings import normalize_narrative_settings

    save_session_narrative_settings(
        db,
        "chat",
        "s1",
        normalize_narrative_settings(style.to_dict() | {"preset": "custom", "ending_mode": "closed_story"}),
    )
    add_story(db, "The rebellion prepares its final confrontation at the gate.")
    run_reconciliation(session_db, lambda *a, **k: packet(arc(), phase="escalation"))
    return db


def ready(db, **versions):
    state = load_ending_state(db, "chat", "s1")
    params = {
        "story_revision": load_narrative_clock(db, "chat", "s1")["state_revision"],
        "expected_lifecycle_revision": state.lifecycle_revision,
        "expected_goal_revision": state.goal_revision,
        "direction_revision": 1,
        "reason": "The major conflict is developed enough for a final confrontation.",
    }
    params.update(versions)
    return mark_finale_ready(db, "chat", "s1", **params)


def test_missing_ending_state_is_a_pure_open_default(session_db):
    _, db, _ = session_db
    before = db.total_changes
    state = load_ending_state(db, "chat", "s1")
    assert state.lifecycle == "open" and state.current_goal == "" and state.goal_revision == 0
    assert load_ending_row(db, "chat", "s1") is None
    assert db.total_changes == before and not db.in_transaction


def test_manual_ending_goal_works_before_story_and_preserves_full_history(session_db):
    _, db, _ = session_db
    first = goal(db, "Mara determines the future of the monarchy.")
    assert first.goal_revision == 1 and first.lifecycle_revision == 1
    second = goal(db, "Let the story find an emergent ending.")
    records = ending_goal_history(db, "chat", "s1")
    assert [r.goal_revision for r in records] == [2, 1]
    assert records[0].previous_goal == first.current_goal and records[0].new_goal == second.current_goal
    assert records[0].source == "user"
    assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    assert ending_goal_history(db, "other", "s1") == []


def test_director_adaptation_is_a_plan_with_auditable_reason(session_db):
    db = ready_case(session_db)
    goal(db, "Mara becomes queen.")
    changed = goal(
        db,
        "Mara determines the monarchy's future.",
        source="director",
        reason="Mara has rejected taking the throne in the committed story.",
    )
    row = ending_goal_history(db, "chat", "s1")[0]
    assert row.source == "director" and row.reason.startswith("Mara has rejected")
    assert changed.current_goal == row.new_goal
    assert db.execute("SELECT status FROM narrative_arcs").fetchone()[0] == "active"


@pytest.mark.parametrize(
    "values",
    [
        {"text": "x" * 4001},
        {"text": None},
        {"source": "ai"},
        {"source": "director", "reason": ""},
        {"reason": "x" * 1001},
        {"story_revision": True},
        {"expected_goal_revision": -1},
        {"expected_lifecycle_revision": 0.1},
    ],
)
def test_invalid_goal_updates_cannot_partially_mutate(session_db, values):
    db = ready_case(session_db)
    prior = goal(db, "Saved goal")
    values = dict(values)
    text = values.pop("text", "New goal")
    with pytest.raises(ValueError):
        goal(db, text, **values)
    assert load_ending_state(db, "chat", "s1") == prior
    assert len(ending_goal_history(db, "chat", "s1")) == 1


@pytest.mark.parametrize("field", ["story_revision", "expected_goal_revision", "expected_lifecycle_revision"])
def test_stale_goal_update_is_rejected(field, session_db):
    db = ready_case(session_db)
    goal(db, "Current goal")
    with pytest.raises(ValueError, match=r"changed|revision"):
        goal(db, "Obsolete goal", **{field: 0})
    assert load_ending_state(db, "chat", "s1").current_goal == "Current goal"


def test_goal_history_is_bounded_without_losing_current_goal(session_db):
    _, db, _ = session_db
    for revision in range(105):
        goal(db, f"Goal {revision}")
    records = ending_goal_history(db, "chat", "s1", limit=10000)
    assert len(records) == 100 and records[0].goal_revision == 105
    assert load_ending_state(db, "chat", "s1").current_goal == "Goal 104"


def test_goal_and_history_rollback_as_one_caller_transaction(session_db):
    _, db, _ = session_db
    db.execute("BEGIN")
    goal(db, "Pending goal")
    assert db.in_transaction and ending_goal_history(db, "chat", "s1")
    db.rollback()
    assert load_ending_state(db, "chat", "s1").goal_revision == 0
    assert ending_goal_history(db, "chat", "s1") == []


@pytest.mark.parametrize(
    "lifecycle", ["finale", "resolution_committed", "epilogue_pending", "epilogue_committed", "closed"]
)
def test_ending_goal_is_locked_once_finale_begins(session_db, lifecycle):
    _, db, _ = session_db
    prior = goal(db, "Original ending goal")
    with write_transaction(db):
        db.execute("UPDATE ending_state SET lifecycle=? WHERE chat_id='chat' AND session_id='s1'", (lifecycle,))
    with pytest.raises(ValueError, match=r"finale|ended|locked"):
        goal(db, "Rewrite canon")
    assert load_ending_state(db, "chat", "s1").current_goal == prior.current_goal


def test_ready_is_idempotent_and_records_required_arcs_and_source_clock(session_db):
    db = ready_case(session_db)
    clock = load_narrative_clock(db, "chat", "s1")
    first = ready(db)
    assert first.lifecycle == "finale_ready" and first.required_arcs == ("rebellion_arc",)
    assert first.finale_ready_revision == clock["state_revision"]
    assert first.ready_history_revision == clock["history_revision"]
    assert first.ready_settings_revision == clock["settings_revision"]
    assert first.ready_through_rowid == clock["latest_rowid"]
    before = db.total_changes
    assert ready(db) == first and db.total_changes == before
    assert db.execute("SELECT COUNT(*) FROM narrative_checkpoints WHERE kind='pre_finale'").fetchone()[0] == 0


@pytest.mark.parametrize("change", ["append", "edit", "delete", "style", "goal"])
def test_unconfirmed_readiness_is_invalidated_by_changed_input_in_same_transaction(session_db, change):
    db = ready_case(session_db)
    before = ready(db)
    db.execute("BEGIN")
    if change == "append":
        add_story(db, "The plot took another turn.")
    elif change == "edit":
        db.execute("UPDATE messages SET content='The final confrontation did not happen.'")
    elif change == "delete":
        db.execute("DELETE FROM messages WHERE chat_id='chat' AND session_id='s1'")
    elif change == "style":
        save_session_narrative_settings(db, "chat", "s1", preset_narrative_settings("observer"))
    else:
        goal(db, "Choose a different ending direction.")
    current = load_ending_state(db, "chat", "s1")
    assert current.lifecycle == "open" and current.finale_ready_revision is None
    assert current.lifecycle_revision > before.lifecycle_revision
    assert db.in_transaction
    db.rollback()
    assert load_ending_state(db, "chat", "s1") == before


def test_delivery_metadata_does_not_invalidate_finale_readiness(session_db):
    db = ready_case(session_db)
    before = ready(db)
    with write_transaction(db):
        db.execute("UPDATE messages SET telegram_message_ids='[12,13]'")
    assert load_ending_state(db, "chat", "s1") == before


@pytest.mark.parametrize("change", ["stale", "open_mode", "early_phase", "wrong_revision"])
def test_finale_readiness_cannot_guess_from_ineligible_state(session_db, change):
    db = ready_case(session_db)
    if change == "stale":
        add_story(db)
    elif change == "open_mode":
        save_session_narrative_settings(db, "chat", "s1", preset_narrative_settings("world_driven"))
    elif change == "early_phase":
        with write_transaction(db):
            db.execute("UPDATE narrative_state SET story_phase='setup'")
    versions = {"story_revision": 0} if change == "wrong_revision" else {}
    with pytest.raises(ValueError):
        ready(db, **versions)
    assert load_ending_state(db, "chat", "s1").lifecycle == "open"


def test_legal_and_illegal_lifecycle_edges_are_explicit():
    path = [
        "open",
        "finale_ready",
        "finale",
        "resolution_committed",
        "epilogue_pending",
        "epilogue_committed",
        "closed",
    ]
    for index, state in enumerate(path):
        for target in path:
            if (index < len(path) - 1 and target == path[index + 1]) or (state == "finale_ready" and target == "open"):
                validate_ending_transition(state, target)
            else:
                with pytest.raises(ValueError):
                    validate_ending_transition(state, target)


def test_ending_repository_enforces_transaction_and_stale_cas(session_db):
    _, db, _ = session_db
    values = asdict(load_ending_state(db, "chat", "s1"))
    with pytest.raises(RuntimeError, match="caller-owned transaction"):
        save_ending_row(db, "chat", "s1", values, expected_revision=0)
    db.execute("BEGIN")
    assert save_ending_row(db, "chat", "s1", values | {"current_goal": "Stored"}, expected_revision=0)
    assert not save_ending_row(db, "chat", "s1", values | {"current_goal": "Stale"}, expected_revision=0)
    assert load_ending_row(db, "chat", "s1")["current_goal"] == "Stored"
    assert db.in_transaction
    db.rollback()
    assert load_ending_row(db, "chat", "s1") is None
