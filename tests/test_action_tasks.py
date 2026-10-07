"""User task state is evidence-derived, reversible, and separate from random results."""

import json

import pytest
from test_simulation_trackers import _assistant_row, _db

from bridge.simulation_checks import perform_check
from bridge.simulation_extraction import parse_simulation_payload
from bridge.simulation_service import SimulationService
from bridge.simulation_view import tracker_view
from bridge.sqlite_store import write_transaction


def task(**changes):
    return {
        "id": "quiet-entry",
        "objective": "Enter the archive",
        "stage": "Door opened",
        "status": "active",
        "progress_current": 1,
        "progress_target": 2,
        "completed_steps": ["Unlock the door"],
        "pending_steps": ["Cross the corridor"],
        "complications": ["Guard nearby"],
        "consequence": "Latch noise heard",
        **changes,
    }


def test_task_changes_publish_without_inventing_a_check():
    db = _db()
    try:
        first = _assistant_row(db, "The door opens, but the corridor remains ahead.")
        payload, valid = parse_simulation_payload(json.dumps({"npcs": [], "simulation": {"tasks": [task()]}}))
        assert valid and payload["tasks"][0]["pending_steps"] == ["Cross the corridor"]
        service = SimulationService()
        service.apply_payload(db, "chat", "s1", payload, source_rowid=first)
        assert service.state(db, "chat", "s1", "task", "quiet-entry")["progress_current"] == 1
        assert "TASK quiet-entry" in service.context_for_prompt(db, "chat", "s1")
        view = tracker_view(db, "chat", "s1")
        assert view["tasks"][0]["stage"] == "Door opened"
        assert db.execute("SELECT count(*) FROM simulation_checks").fetchone()[0] == 0
        later = _assistant_row(db, "The user crosses the corridor.")
        service.apply_payload(
            db, "chat", "s1", {"tasks": [task(status="completed", progress_current=2)]}, source_rowid=later
        )
        service.rollback_from_row(db, "chat", "s1", later)
        assert service.state(db, "chat", "s1", "task", "quiet-entry")["status"] == "active"
    finally:
        db.close()


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "planned"},
        {"actor": "Secret NPC"},
        {"progress_current": True},
        {"pending_steps": "not an array"},
        {"pending_steps": ["step"] * 17},
    ],
)
def test_malformed_or_private_npc_tasks_are_not_accepted(changes):
    _, valid = parse_simulation_payload(json.dumps({"npcs": [], "simulation": {"tasks": [task(**changes)]}}))
    assert not valid


def test_task_check_link_requires_existing_local_check():
    from bridge.simulation_narrative import canonicalize_narrative_links

    db = _db()
    try:
        first = _assistant_row(db)
        payload = {"tasks": [task(last_check_key="other-session-check")]}
        cleaned = canonicalize_narrative_links(db, "chat", "s1", payload, first)
        assert "last_check_key" not in cleaned["tasks"][0]
        with pytest.raises(ValueError):
            SimulationService().apply_payload(db, "chat", "s1", payload, source_rowid=first)
    finally:
        db.close()


def test_checkpoint_restores_task_reference_without_pending_rolls():
    from bridge.simulation_snapshot import restore_simulation_snapshot, snapshot_simulation_state

    db = _db()
    try:
        first = _assistant_row(db)
        perform_check(
            db,
            "chat",
            "s1",
            request_key="auto:entry",
            domain="stealth",
            actor="user",
            action="Open",
            dc=13,
            roll=9,
            source_rowid=first,
        )
        later = _assistant_row(db)
        service = SimulationService()
        service.apply_payload(db, "chat", "s1", {"tasks": [task(last_check_key="auto:entry")]}, source_rowid=later)
        snapshot = snapshot_simulation_state(db, "chat", "s1", later)
        with write_transaction(db):
            restore_simulation_snapshot(db, "chat", "s1", snapshot)
        restored = service.state(db, "chat", "s1", "task", "quiet-entry")
        assert restored["last_check_key"] == "restored:auto:entry"
        assert db.execute("SELECT roll FROM simulation_checks").fetchone()[0] == 9
    finally:
        db.close()
