"""Malformed Utility types reject a whole source without canonical side effects."""

import json

import pytest
from test_memory_completion_safety import session_db as session_db
from test_simulation_integration import refresh
from test_simulation_trackers import _assistant_row

from bridge.npc_repository import get_npc_extraction_coverage, list_npc_entities
from bridge.simulation_extraction import parse_simulation_payload
from bridge.simulation_service import SimulationService


@pytest.mark.parametrize(
    "payload",
    [
        {"quests": [{"id": "gate", "objective": {"nested": "untrusted"}}]},
        {"agendas": [{"npc": "Maya", "max_steps": True}]},
        {"agendas": [{"npc": "Maya", "step": "2"}]},
        {"agendas": [{"npc": "Maya", "complete": None}]},
        {"relationships": [{"npc": "Maya", "sparks_delta": 1.5}]},
        {"relationships": [{"npc": "Maya", "grudge_delta": False}]},
        {"relationships": [{"npc": "Maya", "apology": None}]},
        {"relationships": [{"npc": ["Maya"]}]},
        {"relationships": [{}]},
        {"relationships": [None]},
        {"relationships": None},
        {"actor": False},
        {"actor": None},
        {"actor": {"inventory_remove": None}},
        {"actor": {"inventory_add": [{"name": "Key", "modifier": True}]}},
        {"actor": {"skills_add": [{"name": "Climb", "domain": []}]}},
        {"actor": {"conditions_add": [{"name": None}]}},
        {"actor": {"inventory_add": [""]}},
        {"on_screen_npcs": [123]},
        {"factions": [{"name": "Guard", "lies": [123]}]},
        {"factions": [{"name": "Guard", "relations": {"Guild": {"hostile": True}}}]},
        {"foreshadowing": [{"id": "key", "seed": None}]},
        {"quests": [{"id": "gate", "progress_current": False}]},
    ],
)
def test_supplied_fields_require_their_declared_json_types(payload):
    assert parse_simulation_payload(json.dumps({"npcs": [], "simulation": payload})) == ({}, False)


def test_malformed_typed_part_preserves_existing_state_npcs_and_worker_coverage(session_db):
    _, db, _ = session_db
    first = _assistant_row(db)
    refresh(
        session_db,
        lambda *a, **k: json.dumps(
            {
                "npcs": [],
                "simulation": {
                    "quests": [{"id": "gate", "objective": "Find the brass key"}],
                    "agendas": [{"npc": "Maya", "objective": "Research", "step": 2, "max_steps": 5}],
                },
            }
        ),
    )
    service = SimulationService()
    before = {
        kind: service.state(db, "chat", "s1", kind, name) for kind, name in (("quest", "gate"), ("agenda", "maya"))
    }
    second = _assistant_row(db, "Maya gives a new clue.")
    refresh(
        session_db,
        lambda *a, **k: json.dumps(
            {
                "npcs": [
                    {
                        "name": "New witness",
                        "aliases": [],
                        "operations": [{"field": "role", "op": "set", "value": "Witness", "mode": "mutable"}],
                    }
                ],
                "simulation": {
                    "quests": [{"id": "gate", "objective": {"nested": "untrusted"}}],
                    "agendas": [{"npc": "Maya", "max_steps": True}],
                },
            }
        ),
    )
    assert get_npc_extraction_coverage(db, "chat", "s1") == first
    assert list_npc_entities(db, "chat", "s1") == []
    assert service.state(db, "chat", "s1", "quest", "gate") == before["quest"]
    assert service.state(db, "chat", "s1", "agenda", "maya") == before["agenda"]
    assert db.execute("SELECT source_rowid FROM simulation_sources ORDER BY source_rowid").fetchall() == [(first,)]
    assert second > first
