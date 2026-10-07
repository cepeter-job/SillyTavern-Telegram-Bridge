"""Tracker state follows the canonical transcript and immutable checkpoints."""

from types import SimpleNamespace

import pytest
from test_memory_completion_safety import session_db as session_db
from test_simulation_trackers import _assistant_row

from bridge.npc_service import NpcService
from bridge.simulation_service import SimulationService
from bridge.sqlite_store import write_transaction


def seed(db):
    source = _assistant_row(db)
    SimulationService().apply_payload(db, "chat", "s1", {"actor": {"inventory_add": ["Old key"]}}, source_rowid=source)
    SimulationService.perform_check(
        db,
        "chat",
        "s1",
        request_key="one",
        actor="user",
        domain="lock",
        action="Open lock",
        dc=12,
        roll=15,
        source_rowid=source,
    )
    return source


def test_reset_purges_trackers_checks_and_receipts(session_db, monkeypatch):
    from bridge import message_commands

    _, db, session = session_db
    seed(db)
    monkeypatch.setattr(message_commands, "delete_tracked_panel_messages", lambda *a, **k: None)
    message_commands.reset_session(
        db, "", "chat", session, memory_service=SimpleNamespace(queue_cleanup=lambda *a: None), npc_service=NpcService()
    )
    for table in ("simulation_state", "simulation_state_history", "simulation_checks", "simulation_sources"):
        assert db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 0  # noqa: S608 -- fixed test table names


def test_deleted_session_cascades_every_tracker_table(session_db):
    _, db, _ = session_db
    seed(db)
    with write_transaction(db):
        db.execute("DELETE FROM sessions WHERE chat_id='chat' AND session_id='s1'")
    for table in (
        "simulation_state",
        "simulation_state_history",
        "simulation_checks",
        "simulation_sources",
        "simulation_revisions",
    ):
        assert db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 0  # noqa: S608 -- fixed test table names


@pytest.mark.parametrize("mutation", ["edit", "continue", "delete"])
def test_canonical_rewrite_hides_stale_state_before_extraction_repairs_it(session_db, mutation):
    from bridge.memory_draft_publish import restore_derived

    _, db, _ = session_db
    source = seed(db)
    with write_transaction(db):
        if mutation == "delete":
            db.execute("DELETE FROM messages WHERE id=?", (source,))
        else:
            db.execute("UPDATE messages SET content=? WHERE id=?", ("Rewritten " + mutation, source))
    assert SimulationService.context_for_prompt(db, "chat", "s1") == ""
    with write_transaction(db):
        restore_derived(db, "chat", "s1", "npc", {}, 0)
    assert SimulationService().state(db, "chat", "s1", "actor", "user") is None
    assert db.execute("SELECT COUNT(*) FROM simulation_checks").fetchone()[0] == 0


def test_snapshot_cutoff_remaps_source_and_preserves_branch_isolation(session_db):
    from bridge.checkpoint_remap import remap_checkpoint
    from bridge.session_core import create_session
    from bridge.simulation_snapshot import restore_simulation_snapshot, snapshot_simulation_state

    config, db, _ = session_db
    first = seed(db)
    second = _assistant_row(db)
    SimulationService().apply_payload(
        db, "chat", "s1", {"actor": {"inventory_add": ["Future map"]}}, source_rowid=second
    )
    snapshot = snapshot_simulation_state(db, "chat", "s1", first)
    create_session(db, "chat", "dummy::model", session_id="branch", app_settings=config)
    with write_transaction(db):
        target = db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) "
            "SELECT chat_id,'branch',role,content,created_at FROM messages WHERE id=?",
            (first,),
        ).lastrowid
        restore_simulation_snapshot(db, "chat", "branch", remap_checkpoint(snapshot, {first: target, 0: 0}))
    branch = SimulationService().state(db, "chat", "branch", "actor", "user")
    assert [item["name"] for item in branch["inventory"]] == ["Old key"]
    check = db.execute(
        "SELECT source_rowid,roll,request_key FROM simulation_checks WHERE session_id='branch'"
    ).fetchone()
    assert check[0:2] == (target, 15) and check[2] != "one"
    SimulationService().purge_session(db, "chat", "s1")
    assert SimulationService().state(db, "chat", "branch", "actor", "user") == branch
    SimulationService().rollback_from_row(db, "chat", "branch", target)
    assert SimulationService().state(db, "chat", "branch", "actor", "user") is None


def test_clear_revision_fences_a_captured_npc_draft_even_when_empty(session_db):
    from bridge.memory_draft_store import _publication_snapshot

    _, db, _ = session_db
    claim = SimpleNamespace(chat_id="chat", session_id="s1", layer="npc")
    before = _publication_snapshot(db, claim)
    SimulationService().purge_session(db, "chat", "s1")
    assert _publication_snapshot(db, claim) != before


def test_tracker_source_must_belong_to_its_session(session_db):
    _, db, _ = session_db
    source = seed(db)
    with pytest.raises(ValueError, match="source"):
        SimulationService().apply_payload(db, "another-chat", "s1", {}, source_rowid=source)


def test_swipe_selection_removes_discarded_tracker_suffix(session_db):
    from bridge.response_variants import keep_swipe_variant, save_response_variant

    _, db, _ = session_db
    with write_transaction(db):
        user = db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) "
            "VALUES('chat','s1','user','Open the door',1)"
        ).lastrowid
    seed(db)
    index = save_response_variant(db, "chat", "s1", "Open the door", "Another outcome", user_rowid=user)
    assert keep_swipe_variant(db, "chat", "s1", index, npc_service=NpcService()) == "Another outcome"
    assert SimulationService().state(db, "chat", "s1", "actor", "user") is None


def test_finale_capture_contains_checkpoint_tracker_state(session_db):
    from test_narrative_checkpoints import enter, prepared

    db = prepared(session_db)
    source = db.execute("SELECT MAX(id) FROM messages").fetchone()[0]
    SimulationService().apply_payload(
        db, "chat", "s1", {"actor": {"inventory_add": ["Finale key"]}}, source_rowid=source
    )
    checkpoint = enter(db)
    record = checkpoint.payload["memory"]["simulation"]["records"][0]
    assert record["source_rowid"] == source
    assert record["value"]["entries"][0]["name"] == "Finale key"
