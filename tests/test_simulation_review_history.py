"""Branch restoration preserves every bounded tracker revision and receipt."""

import pytest
from test_memory_completion_safety import session_db as session_db
from test_simulation_trackers import _assistant_row

from bridge.checkpoint_remap import remap_checkpoint
from bridge.session_core import create_session
from bridge.simulation_service import SimulationService
from bridge.simulation_snapshot import restore_simulation_snapshot, snapshot_simulation_state
from bridge.sqlite_store import write_transaction


def test_restored_history_keeps_intermediate_modifiers_and_suffix_rollback(session_db):
    config, db, _ = session_db
    service = SimulationService()
    old_key = {"actor": {"inventory_add": [{"name": "Old key", "domain": "lock", "modifier": 1}]}}
    first = _assistant_row(db, "The user receives an old key.")
    service.apply_payload(db, "chat", "s1", old_key, source_rowid=first)
    with write_transaction(db):
        middle = db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('chat','s1','user','Go north',2)"
        ).lastrowid
    second = _assistant_row(db, "The user finds a map.")
    service.apply_payload(
        db,
        "chat",
        "s1",
        {"actor": {"inventory_add": [{"name": "Map", "domain": "lock", "modifier": 2}]}},
        source_rowid=second,
    )
    snapshot = snapshot_simulation_state(db, "chat", "s1", second)
    create_session(db, "chat", "dummy::model", session_id="branch", app_settings=config)
    rowids = {0: 0}
    with write_transaction(db):
        for source in (first, middle, second):
            rowids[source] = db.execute(
                "INSERT INTO messages(chat_id,session_id,role,content,created_at) "
                "SELECT chat_id,'branch',role,content,created_at FROM messages WHERE id=?",
                (source,),
            ).lastrowid
        restore_simulation_snapshot(db, "chat", "branch", remap_checkpoint(snapshot, rowids))
    origin = service.state(db, "chat", "s1", "actor", "user")
    assert service.state(db, "chat", "branch", "actor", "user") == origin
    historical = service.state(db, "chat", "s1", "actor", "user", through_rowid=middle)
    assert service.state(db, "chat", "branch", "actor", "user", through_rowid=rowids[middle]) == historical
    assert service.actor_modifier(db, "chat", "branch", "lock", through_rowid=rowids[middle]) == 1
    service.rollback_from_row(db, "chat", "branch", rowids[second])
    service.apply_payload(db, "chat", "branch", old_key, source_rowid=rowids[first])
    assert service.state(db, "chat", "branch", "actor", "user") == historical
    assert service.state(db, "chat", "s1", "actor", "user") == origin
    assert db.execute("SELECT source_rowid FROM simulation_sources WHERE session_id='branch'").fetchall() == [
        (rowids[first],)
    ]


def test_checkpoint_rejects_large_history_even_when_current_state_fits(session_db):
    from bridge.simulation_repository import store_state

    _, db, _ = session_db
    source = _assistant_row(db)
    with write_transaction(db):
        for revision in range(90):
            store_state(
                db,
                "chat",
                "s1",
                "quest",
                "long-lived",
                {"revision": revision, "objective": "é" * 7000},
                source_rowid=source,
                now=revision,
            )
    with pytest.raises(ValueError, match="checkpoint"):
        snapshot_simulation_state(db, "chat", "s1", source)


def test_alias_tombstone_restores_with_its_historical_identity_transition(session_db):
    from test_simulation_projection import npc

    config, db, _ = session_db
    service = SimulationService()
    first = _assistant_row(db)
    service.apply_payload(db, "chat", "s1", {"relationships": [{"npc": "Maya", "sparks_delta": 2}]}, source_rowid=first)
    second = _assistant_row(db)
    npc(db, second)
    service.apply_payload(db, "chat", "s1", {}, source_rowid=second)
    snapshot = snapshot_simulation_state(db, "chat", "s1", second)
    create_session(db, "chat", "dummy::model", session_id="branch", app_settings=config)
    rowids = {0: 0}
    with write_transaction(db):
        for source in (first, second):
            rowids[source] = db.execute(
                "INSERT INTO messages(chat_id,session_id,role,content,created_at) "
                "SELECT chat_id,'branch',role,content,created_at FROM messages WHERE id=?",
                (source,),
            ).lastrowid
        restore_simulation_snapshot(db, "chat", "branch", remap_checkpoint(snapshot, rowids))
    assert service.state(db, "chat", "branch", "relationship", "maya") is None
    assert service.state(db, "chat", "branch", "relationship", "maya torres")["sparks"] == 2
    assert service.state(db, "chat", "branch", "relationship", "maya", through_rowid=rowids[first])["sparks"] == 2
    service.rollback_from_row(db, "chat", "branch", rowids[second])
    assert service.state(db, "chat", "branch", "relationship", "maya")["sparks"] == 2
    assert service.state(db, "chat", "branch", "relationship", "maya torres") is None
