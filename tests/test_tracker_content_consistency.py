"""Synthetic public-behavior contracts for tracker content consistency."""

import pytest
from test_simulation_trackers import _assistant_row, _db

from bridge.simulation_repository import list_states
from bridge.simulation_service import SimulationService


@pytest.mark.parametrize("kind,group", [("task", "tasks"), ("quest", "quests")])
def test_punctuation_variant_reuses_existing_identity_and_preserves_rollback(kind, group):
    db = _db()
    service = SimulationService()
    try:
        first = _assistant_row(db)
        service.apply_payload(
            db,
            "chat",
            "s1",
            {
                group: [
                    {
                        "id": "Morgan's Keys",
                        "objective": "Return the borrowed keys",
                        "status": "active",
                        "progress_current": 1,
                        "progress_target": 3,
                    }
                ]
            },
            source_rowid=first,
        )
        original = list_states(db, "chat", "s1", kind=kind)[0]
        second = _assistant_row(db)
        service.apply_payload(
            db,
            "chat",
            "s1",
            {group: [{"id": "morgans-keys", "objective": "Return the borrowed keys", "progress_current": 2}]},
            source_rowid=second,
        )
        states = list_states(db, "chat", "s1", kind=kind)
        assert len(states) == 1
        assert states[0][1] == original[1]
        assert states[0][2]["progress_current"] == 2
        assert service.state(db, "chat", "s1", kind, original[1], through_rowid=first)["progress_current"] == 1
        service.rollback_from_row(db, "chat", "s1", second)
        restored = list_states(db, "chat", "s1", kind=kind)
        assert len(restored) == 1
        assert restored[0][2]["progress_current"] == 1
    finally:
        db.close()


def test_completed_step_removes_retained_pending_without_guessing_paraphrases():
    db = _db()
    service = SimulationService()
    try:
        first = _assistant_row(db)
        service.apply_payload(
            db,
            "chat",
            "s1",
            {
                "tasks": [
                    {
                        "id": "archive-visit",
                        "objective": "Visit the archive",
                        "status": "active",
                        "progress_current": 1,
                        "progress_target": 2,
                        "pending_steps": ["Unlock the door", "Enter the archive", "Open the door"],
                    }
                ]
            },
            source_rowid=first,
        )
        second = _assistant_row(db)
        service.apply_payload(
            db,
            "chat",
            "s1",
            {"tasks": [{"id": "archive-visit", "completed_steps": ["  UNLOCK the door.  "], "progress_current": 2}]},
            source_rowid=second,
        )
        state = service.state(db, "chat", "s1", "task", "archive-visit")
        assert state["pending_steps"] == ["Enter the archive", "Open the door"]
        assert state["status"] == "active"
        historical = service.state(db, "chat", "s1", "task", "archive-visit", through_rowid=first)
        assert historical["pending_steps"] == ["Unlock the door", "Enter the archive", "Open the door"]
        service.rollback_from_row(db, "chat", "s1", second)
        assert service.state(db, "chat", "s1", "task", "archive-visit") == historical
    finally:
        db.close()


def test_actor_name_is_not_added_as_inventory_and_existing_entries_are_not_purged():
    db = _db()
    service = SimulationService()
    try:
        first = _assistant_row(db)
        # Model a legacy bad item; publication must not silently rewrite accepted history.
        service.apply_payload(db, "chat", "s1", {"actor": {"inventory_add": ["Casey"]}}, source_rowid=first)
        second = _assistant_row(db)
        service.apply_payload(
            db,
            "chat",
            "s1",
            {"actor": {"inventory_add": ["CASEY", "Casey's notebook", "Brass key"]}},
            source_rowid=second,
            user_name=" Casey ",
        )
        inventory = service.state(db, "chat", "s1", "actor", "user")["inventory"]
        assert [entry["name"] for entry in inventory] == ["Casey", "Casey's notebook", "Brass key"]
        assert service.state(db, "chat", "s1", "actor", "user", through_rowid=first)["inventory"] == [
            {"name": "Casey", "domain": "any", "modifier": 0}
        ]
    finally:
        db.close()


def test_new_actor_collection_ignores_self_name_but_keeps_other_items():
    db = _db()
    try:
        row = _assistant_row(db)
        service = SimulationService()
        service.apply_payload(
            db,
            "chat",
            "s1",
            {"actor": {"inventory_add": ["Casey", "Brass key"]}},
            source_rowid=row,
            user_name="Casey",
        )
        assert [entry["name"] for entry in service.state(db, "chat", "s1", "actor", "user")["inventory"]] == [
            "Brass key"
        ]
    finally:
        db.close()


def test_task_extraction_context_contains_completed_steps_for_pending_reconciliation():
    from bridge.simulation_prompt import extraction_tracker_context

    db = _db()
    try:
        row = _assistant_row(db)
        SimulationService().apply_payload(
            db,
            "chat",
            "s1",
            {
                "tasks": [
                    {
                        "id": "archive-visit",
                        "objective": "Visit the archive",
                        "completed_steps": ["Bought the access pass"],
                        "pending_steps": ["Enter the archive"],
                    }
                ]
            },
            source_rowid=row,
        )
        context = extraction_tracker_context(db, "chat", "s1", row)
        assert "Bought the access pass" in context
        assert "Enter the archive" in context
        assert "TASK archive-visit" in context
    finally:
        db.close()


@pytest.mark.parametrize("kind,group", [("task", "tasks"), ("quest", "quests")])
@pytest.mark.parametrize(
    "incoming",
    [
        {"id": "morgans-keys", "objective": "Make another copy"},
        {"id": "morgans-keys"},
        {"id": "unrelated-job", "objective": "Return the borrowed keys"},
    ],
)
def test_identity_variants_require_matching_objective_and_related_id(kind, group, incoming):
    db = _db()
    try:
        service = SimulationService()
        first = _assistant_row(db)
        service.apply_payload(
            db,
            "chat",
            "s1",
            {group: [{"id": "Morgan's Keys", "objective": "Return the borrowed keys"}]},
            source_rowid=first,
        )
        second = _assistant_row(db)
        service.apply_payload(db, "chat", "s1", {group: [incoming]}, source_rowid=second)
        assert len(list_states(db, "chat", "s1", kind=kind)) == 2
    finally:
        db.close()


@pytest.mark.parametrize("kind,group", [("task", "tasks"), ("quest", "quests")])
def test_ambiguous_legacy_identity_rolls_back_entire_publication(kind, group):
    from bridge.simulation_repository import store_state
    from bridge.sqlite_store import write_transaction

    db = _db()
    try:
        service = SimulationService()
        first = _assistant_row(db)
        service.apply_payload(
            db,
            "chat",
            "s1",
            {"relationships": [{"npc": "Guide", "sparks_delta": 1}]},
            source_rowid=first,
        )
        with write_transaction(db):
            for name in ("morgan-s-keys", "morgans-keys"):
                store_state(
                    db,
                    "chat",
                    "s1",
                    kind,
                    name,
                    {"display_name": name, "objective": "Return the borrowed keys", "progress_current": 1},
                    source_rowid=first,
                    now=1,
                )
        before = list_states(db, "chat", "s1")
        history_count = db.execute("SELECT COUNT(*) FROM simulation_state_history").fetchone()[0]
        second = _assistant_row(db)
        with pytest.raises(ValueError, match="Ambiguous tracker identity"):
            service.apply_payload(
                db,
                "chat",
                "s1",
                {
                    "relationships": [{"npc": "Guide", "sparks_delta": 1}],
                    "actor": {"inventory_add": ["Brass key"]},
                    group: [{"id": "m-o-r-g-a-n-s-keys", "objective": "Return the borrowed keys"}],
                },
                source_rowid=second,
            )
        assert list_states(db, "chat", "s1") == before
        assert db.execute("SELECT COUNT(*) FROM simulation_state_history").fetchone()[0] == history_count
        assert db.execute("SELECT COUNT(*) FROM simulation_sources WHERE source_rowid=?", (second,)).fetchone()[0] == 0
    finally:
        db.close()


def test_legacy_punctuation_key_is_not_renamed_and_source_replay_is_idempotent():
    from bridge.simulation_repository import store_state
    from bridge.sqlite_store import write_transaction

    db = _db()
    try:
        service = SimulationService()
        first = _assistant_row(db)
        service.apply_payload(db, "chat", "s1", {}, source_rowid=first)
        with write_transaction(db):
            store_state(
                db,
                "chat",
                "s1",
                "task",
                "morgan's-keys",
                {"display_name": "Morgan's Keys", "objective": "Return the borrowed keys", "progress_current": 1},
                source_rowid=first,
                now=1,
            )
        second = _assistant_row(db)
        payload = {"tasks": [{"id": "morgans-keys", "objective": "RETURN   the borrowed keys", "progress_current": 2}]}
        service.apply_payload(db, "chat", "s1", payload, source_rowid=second)
        states = list_states(db, "chat", "s1", kind="task")
        assert len(states) == 1 and states[0][1] == "morgan's-keys"
        assert states[0][2]["display_name"] == "Morgan's Keys"
        service.apply_payload(db, "chat", "s1", payload, source_rowid=second)
        assert list_states(db, "chat", "s1", kind="task") == states
        service.rollback_from_row(db, "chat", "s1", second)
        assert service.state(db, "chat", "s1", "task", "morgan's-keys")["progress_current"] == 1
    finally:
        db.close()


def test_same_source_parts_reuse_identity_without_summing_progress():
    db = _db()
    try:
        row = _assistant_row(db)
        service = SimulationService()
        service.apply_payload(
            db,
            "chat",
            "s1",
            {
                "tasks": [
                    {"id": "Morgan's Keys", "objective": "Return the keys", "progress_current": 1, "status": "active"},
                    {"id": "morgans-keys", "objective": "Return the keys", "progress_current": 2},
                ]
            },
            source_rowid=row,
        )
        states = list_states(db, "chat", "s1", kind="task")
        assert len(states) == 1
        assert states[0][2]["progress_current"] == 2
        assert states[0][2]["status"] == "active"
    finally:
        db.close()


def test_task_lists_reconcile_same_payload_and_preserve_unmatched_steps():
    db = _db()
    try:
        row = _assistant_row(db)
        service = SimulationService()
        service.apply_payload(
            db,
            "chat",
            "s1",
            {
                "tasks": [
                    {
                        "id": "archive",
                        "completed_steps": ["Buy the pass"],
                        "pending_steps": ["buy THE pass!", "Purchase access", "Return tomorrow"],
                        "progress_current": 3,
                        "progress_target": 3,
                        "status": "active",
                    }
                ]
            },
            source_rowid=row,
        )
        state = service.state(db, "chat", "s1", "task", "archive")
        assert state["pending_steps"] == ["Purchase access", "Return tomorrow"]
        assert state["status"] == "active"
    finally:
        db.close()


@pytest.mark.parametrize("link", ["arc_id", "thread_id"])
def test_identity_resolution_does_not_combine_distinct_plot_links(link):
    from bridge.simulation_consistency import resolve_named_identity
    from bridge.simulation_repository import store_state
    from bridge.sqlite_store import write_transaction

    db = _db()
    try:
        with write_transaction(db):
            store_state(
                db,
                "chat",
                "s1",
                "quest",
                "morgan-s-keys",
                {"objective": "Return the keys", link: "plot-a"},
                source_rowid=1,
                now=1,
            )
        name, current = resolve_named_identity(
            db,
            "chat",
            "s1",
            "quest",
            "morgans-keys",
            {"objective": "Return the keys", link: "plot-b"},
        )
        assert name == "morgans-keys"
        assert current is None
    finally:
        db.close()


@pytest.mark.parametrize("kind,group", [("task", "tasks"), ("quest", "quests")])
@pytest.mark.parametrize("other_chat,other_session", [("chat", "s2"), ("other-chat", "s1")])
def test_identity_matching_never_crosses_chat_or_session(kind, group, other_chat, other_session):
    db = _db()
    try:
        db.execute(
            "INSERT INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,world_file,"
            "author_note,system_prompt,response_language,created_at,updated_at) "
            "SELECT ?,?,title,character_file,model_id,persona_id,world_file,author_note,system_prompt,"
            "response_language,created_at,updated_at FROM sessions WHERE chat_id=? AND session_id=?",
            (other_chat, other_session, "chat", "s1"),
        )
        row = db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            (other_chat, other_session, "assistant", "Story beat.", 1),
        ).lastrowid
        assert row is not None
        db.commit()
        service = SimulationService()
        service.apply_payload(
            db,
            other_chat,
            other_session,
            {group: [{"id": "Morgan's Keys", "objective": "Return the keys"}]},
            source_rowid=row,
        )
        old = list_states(db, other_chat, other_session, kind=kind)
        second = _assistant_row(db)
        service.apply_payload(
            db,
            "chat",
            "s1",
            {group: [{"id": "morgans-keys", "objective": "Return the keys"}]},
            source_rowid=second,
        )
        states = list_states(db, "chat", "s1", kind=kind)
        assert len(states) == 1 and states[0][1] == "morgans-keys"
        assert list_states(db, other_chat, other_session, kind=kind) == old
    finally:
        db.close()


@pytest.mark.parametrize("kind,group", [("task", "tasks"), ("quest", "quests")])
def test_distinct_long_objectives_keep_separate_records(kind, group):
    db = _db()
    try:
        service = SimulationService()
        prefix = "Document the source and destination of " + "the northern archive records " * 7
        assert len(prefix.encode()) > 160
        first = _assistant_row(db)
        service.apply_payload(
            db,
            "chat",
            "s1",
            {group: [{"id": "Morgan's Keys", "objective": prefix + "in the east vault"}]},
            source_rowid=first,
        )
        second = _assistant_row(db)
        service.apply_payload(
            db,
            "chat",
            "s1",
            {group: [{"id": "morgans-keys", "objective": prefix + "in the west vault"}]},
            source_rowid=second,
        )
        states = list_states(db, "chat", "s1", kind=kind)
        assert len(states) == 2
        assert {value["objective"] for _, _, value, _, _ in states} == {
            prefix + "in the east vault",
            prefix + "in the west vault",
        }
    finally:
        db.close()


def test_completed_step_does_not_remove_distinct_long_pending_step():
    db = _db()
    try:
        service = SimulationService()
        prefix = "Inspect " + "the northern archive dossier " * 6
        assert 160 < len(prefix.encode()) < 210
        completed = prefix + "blue record"
        unfinished = prefix + "red record"
        source = _assistant_row(db)
        service.apply_payload(
            db,
            "chat",
            "s1",
            {
                "tasks": [
                    {
                        "id": "archive-visit",
                        "completed_steps": [completed],
                        "pending_steps": [unfinished, completed + " !"],
                    }
                ]
            },
            source_rowid=source,
        )
        value = service.state(db, "chat", "s1", "task", "archive-visit")
        assert value["pending_steps"] == [unfinished]
    finally:
        db.close()
