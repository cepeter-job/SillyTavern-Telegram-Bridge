import json

import pytest
from test_simulation_trackers import _assistant_row, _db

from bridge.simulation_extraction import merge_simulation_payload, parse_simulation_payload
from bridge.simulation_service import SimulationService


@pytest.fixture
def db():
    connection = _db()
    try:
        yield connection
    finally:
        connection.close()


@pytest.mark.parametrize("first,last,want", [("add", "remove", []), ("remove", "add", ["Key"])])
def test_actor_parts_preserve_chronological_last_operation(db, first, last, want):
    service = SimulationService()
    source = _assistant_row(db)
    payload = merge_simulation_payload(
        {"actor": {f"inventory_{first}": [{"name": "Key"}]}},
        {"actor": {f"inventory_{last}": [{"name": "Key"}]}},
    )
    service.apply_payload(db, "chat", "s1", payload, source_rowid=source)
    state = service.state(db, "chat", "s1", "actor", "user")
    assert [item["name"] for item in state["inventory"]] == want


def test_relationship_parts_accumulate_once_with_per_turn_cap(db):
    source = _assistant_row(db)
    first = {"relationships": [{"npc": "Maya", "sparks_delta": 1}]}
    merged = merge_simulation_payload(first, first)
    SimulationService().apply_payload(db, "chat", "s1", merged, source_rowid=source)
    assert SimulationService().state(db, "chat", "s1", "relationship", "maya")["sparks"] == 2


def test_distinct_valid_parts_can_publish_more_than_one_source_part(db):
    source = _assistant_row(db)
    first = {"relationships": [{"npc": f"Person {i}", "sparks_delta": 1} for i in range(32)]}
    second = {"relationships": [{"npc": "Final person", "sparks_delta": 1}]}
    merged = merge_simulation_payload(first, second)
    SimulationService().apply_payload(db, "chat", "s1", merged, source_rowid=source)
    assert db.execute("SELECT COUNT(*) FROM simulation_state WHERE kind='relationship'").fetchone()[0] == 33


def test_aggregate_source_parts_reject_overflow_without_discarding_existing_work():
    first = {"relationships": [{"npc": f"Person {i}", "sparks_delta": 1} for i in range(64)]}
    with pytest.raises(ValueError, match=r"bound|limit"):
        merge_simulation_payload(first, {"relationships": [{"npc": "Beyond the limit", "sparks_delta": 1}]})
    assert len(first["relationships"]) == 64


@pytest.mark.parametrize("invalid", ["false", "true", 1, {}, []])
def test_malformed_boolean_does_not_become_an_apology(invalid):
    _payload, valid = parse_simulation_payload(
        json.dumps({"simulation": {"relationships": [{"npc": "Maya", "apology": invalid}]}})
    )
    assert valid is False


def test_long_npc_name_uses_same_identity_for_agenda_and_on_screen(db):
    service = SimulationService()
    name = "M" * 150
    first = _assistant_row(db)
    service.apply_payload(
        db,
        "chat",
        "s1",
        {
            "agendas": [{"npc": name, "objective": "Read", "max_steps": 5}],
        },
        source_rowid=first,
    )
    second = _assistant_row(db)
    service.apply_payload(db, "chat", "s1", {"on_screen_npcs": [name]}, source_rowid=second)
    assert service.state(db, "chat", "s1", "agenda", name)["step"] == 0


def test_partial_quest_update_keeps_existing_objective_and_reward(db):
    service = SimulationService()
    first = _assistant_row(db)
    service.apply_payload(
        db,
        "chat",
        "s1",
        {"quests": [{"id": "gate", "objective": "Open gate", "reward": "Passage", "status": "active"}]},
        source_rowid=first,
    )
    second = _assistant_row(db)
    service.apply_payload(db, "chat", "s1", {"quests": [{"id": "gate", "status": "completed"}]}, source_rowid=second)
    state = service.state(db, "chat", "s1", "quest", "gate")
    assert (state["objective"], state["reward"], state["status"]) == ("Open gate", "Passage", "completed")


def test_oversized_record_is_rejected_by_parser_before_publication():
    _payload, valid = parse_simulation_payload(
        json.dumps(
            {
                "simulation": {
                    "factions": [
                        {
                            "name": "Watch",
                            "goal": "x" * 1000,
                            "intel": "x" * 1000,
                            "conflict": "x" * 1000,
                            "lies": ["x" * 240] * 32,
                            "relations": {(str(i) + "x" * 117): "x" * 240 for i in range(32)},
                        }
                    ]
                }
            }
        )
    )
    assert valid is False


def test_duplicate_relationships_share_one_turn_limit(db):
    source = _assistant_row(db)
    SimulationService().apply_payload(
        db,
        "chat",
        "s1",
        {
            "relationships": [{"npc": "Maya", "sparks_delta": 2}, {"npc": "MAYA", "sparks_delta": 2}],
        },
        source_rowid=source,
    )
    assert SimulationService().state(db, "chat", "s1", "relationship", "maya")["sparks"] == 2


def test_partial_agenda_update_keeps_objective_and_prevents_automatic_tick(db):
    service = SimulationService()
    first = _assistant_row(db)
    service.apply_payload(
        db,
        "chat",
        "s1",
        {
            "agendas": [{"npc": "Maya", "objective": "Read", "max_steps": 5}],
        },
        source_rowid=first,
    )
    second = _assistant_row(db)
    service.apply_payload(db, "chat", "s1", {"agendas": [{"npc": "Maya", "status": "paused"}]}, source_rowid=second)
    state = service.state(db, "chat", "s1", "agenda", "maya")
    assert (state["objective"], state["status"], state["step"]) == ("Read", "paused", 0)
