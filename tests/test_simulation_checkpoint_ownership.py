"""Restored NPC field history and tracker ownership share the same rewind boundary."""

import pytest
from test_memory_completion_safety import session_db as session_db
from test_npc_service import _group, _op
from test_simulation_projection import npc
from test_simulation_trackers import _assistant_row

from bridge.checkpoint_remap import remap_checkpoint
from bridge.memory_draft_publish import restore_derived
from bridge.npc_repository import list_npc_entities, load_npc_fields, restore_npc_snapshot, snapshot_npc_state
from bridge.npc_service import NpcService
from bridge.session_core import create_session
from bridge.simulation_service import SimulationService
from bridge.simulation_snapshot import restore_simulation_snapshot, snapshot_simulation_state
from bridge.sqlite_store import write_transaction


def clone(db, config, sources):
    snapshot = {
        "npcs": snapshot_npc_state(db, "chat", "s1", sources[-1]),
        "simulation": snapshot_simulation_state(db, "chat", "s1", sources[-1]),
    }
    create_session(db, "chat", "dummy::model", session_id="branch", app_settings=config)
    rowids = {0: 0}
    with write_transaction(db):
        for source in sources:
            rowids[source] = db.execute(
                "INSERT INTO messages(chat_id,session_id,role,content,created_at) "
                "SELECT chat_id,'branch',role,content,created_at FROM messages WHERE id=?",
                (source,),
            ).lastrowid
        restored = remap_checkpoint(snapshot, rowids)
        restore_npc_snapshot(db, "chat", "branch", restored["npcs"])
        restore_simulation_snapshot(db, "chat", "branch", restored["simulation"])
    return rowids, list_npc_entities(db, "chat", "branch")[0]


def test_combined_checkpoint_rewind_retains_native_projection_ownership(session_db):
    config, db, _ = session_db
    service = SimulationService()
    first = _assistant_row(db)
    npc(db, first)
    service.apply_payload(
        db, "chat", "s1", {"agendas": [{"npc": "Maya", "objective": "Research", "max_steps": 5}]}, source_rowid=first
    )
    second = _assistant_row(db)
    service.apply_payload(db, "chat", "s1", {}, source_rowid=second)
    rowids, entity = clone(db, config, (first, second))
    assert "1/5" in load_npc_fields(db, entity.npc_id)["agenda"].value
    with write_transaction(db):
        db.execute("UPDATE messages SET content='Rewritten source' WHERE id=?", (rowids[second],))
        restore_derived(db, "chat", "branch", "npc", {}, rowids[first])
    assert service.state(db, "chat", "branch", "agenda", "maya torres")["step"] == 0
    assert "0/5" in load_npc_fields(db, entity.npc_id)["agenda"].value
    service.apply_payload(db, "chat", "branch", {}, source_rowid=rowids[second])
    assert service.state(db, "chat", "branch", "agenda", "maya torres")["step"] == 1
    assert "1/5" in load_npc_fields(db, entity.npc_id)["agenda"].value
    assert service.state(db, "chat", "s1", "agenda", "maya torres")["step"] == 1


@pytest.mark.parametrize("override", ["manual", "clear"])
def test_combined_checkpoint_keeps_real_native_overrides_and_clears(session_db, override):
    config, db, _ = session_db
    service = SimulationService()
    first = _assistant_row(db)
    origin = npc(db, first)
    service.apply_payload(
        db, "chat", "s1", {"agendas": [{"npc": "Maya", "objective": "Research", "max_steps": 5}]}, source_rowid=first
    )
    second = _assistant_row(db)
    if override == "manual":
        npc(db, second, [_op("agenda", "Manual objective", visibility="restricted", known_by=["Alice"])])
    else:
        assert NpcService().undo_latest_field_change(db, "chat", "s1", origin.npc_id, "agenda")
    service.apply_payload(db, "chat", "s1", {}, source_rowid=second)
    _, entity = clone(db, config, (first, second))
    before = load_npc_fields(db, entity.npc_id).get("agenda")
    if override == "manual":
        assert before.value == "Manual objective" and before.known_by == ("Alice",)
    else:
        assert before is None
    with write_transaction(db):
        source = db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) "
            "VALUES('chat','branch','assistant','Next story beat',3)"
        ).lastrowid
    service.apply_payload(db, "chat", "branch", {}, source_rowid=source)
    assert service.state(db, "chat", "branch", "agenda", "maya torres")["step"] == 2
    assert load_npc_fields(db, entity.npc_id).get("agenda") == before


def test_checkpoint_rejects_large_native_history_even_with_small_current_fields(session_db):
    _, db, _ = session_db
    for revision in range(64):
        source = _assistant_row(db)
        NpcService().apply_group(
            db,
            "chat",
            "s1",
            _group(operations=[_op("role", f"{revision} " + "字" * 5000)]),
            source_rowid=source,
            primary_name="Alice",
            user_name="User",
        )
    with pytest.raises(ValueError, match="NPC history"):
        snapshot_npc_state(db, "chat", "s1", source)
