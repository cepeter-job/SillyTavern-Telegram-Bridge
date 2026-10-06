import json
import sqlite3
import time

from bridge.memory_draft_publish import publish_derived
from bridge.schema import initialize_database_schema
from bridge.simulation_extraction import merge_simulation_payload, parse_simulation_payload
from bridge.simulation_service import SimulationService
from bridge.sqlite_store import write_transaction


def _db():
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys=ON")
    initialize_database_schema(db)
    now = time.time()
    db.execute(
        """
        INSERT INTO sessions(
            chat_id,session_id,title,character_file,model_id,persona_id,world_file,
            author_note,system_prompt,response_language,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        ("chat", "s1", "s1", "char.png", "p::m", "", "", "", "", "auto", now, now),
    )
    db.execute(
        "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
        ("chat", "s1", "assistant", "Maya accepts the brass key.", now),
    )
    db.commit()
    return db


def test_parse_simulation_payload_bounds_and_normalizes_model_json():
    raw = json.dumps(
        {
            "npcs": [],
            "simulation": {
                "relationships": [{"npc": " Maya  Torres ", "sparks_delta": 9, "grudge_delta": 4}],
                "agendas": [
                    {
                        "npc": "Maya Torres",
                        "objective": "  Study the archive ",
                        "step": 1,
                        "max_steps": 3,
                        "location": "Library",
                    }
                ],
                "on_screen_npcs": [" Maya Torres "],
                "actor": {
                    "inventory_add": [{"name": "Brass key", "domain": "utility", "modifier": 8}],
                },
                "quests": [
                    {
                        "id": "gate",
                        "kind": "main",
                        "status": "active",
                        "objective": "Open gate",
                        "progress_current": 1,
                        "progress_target": 3,
                        "reward": "Passage",
                    }
                ],
            },
        }
    )
    payload, valid = parse_simulation_payload(raw)
    assert valid is True
    assert payload["relationships"][0]["npc"] == "Maya Torres"
    assert payload["relationships"][0]["sparks_delta"] == 2
    assert payload["relationships"][0]["grudge_delta"] == 1
    assert payload["actor"]["inventory_add"][0]["modifier"] == 2
    assert payload["agendas"][0]["objective"] == "Study the archive"


def test_merge_simulation_payload_accumulates_source_parts_without_losing_actor_updates():
    first = {
        "relationships": [{"npc": "Maya", "sparks_delta": 1}],
        "actor": {"inventory_add": [{"name": "Key"}]},
        "on_screen_npcs": ["Maya"],
    }
    second = {
        "relationships": [{"npc": "Jon", "grudge_delta": 1}],
        "actor": {"conditions_add": [{"name": "Tired"}]},
        "on_screen_npcs": ["Jon"],
    }
    merged = merge_simulation_payload(first, second)
    assert [item["npc"] for item in merged["relationships"]] == ["Maya", "Jon"]
    assert merged["actor"]["inventory_add"] == [{"name": "Key"}]
    assert merged["actor"]["conditions_add"] == [{"name": "Tired"}]
    assert merged["on_screen_npcs"] == ["Maya", "Jon"]


def test_npc_publish_commits_simulation_state_in_same_derived_layer():
    db = _db()
    try:
        payload = {
            "npcs": [],
            "primary_name": "Alice",
            "user_name": "User",
            "simulation": {
                "relationships": [{"npc": "Maya", "sparks_delta": 1}],
                "actor": {"inventory_add": [{"name": "Brass key", "domain": "utility", "modifier": 1}]},
            },
        }
        with write_transaction(db):
            publish_derived(db, "chat", "s1", "npc", payload, 1)
        service = SimulationService()
        assert service.state(db, "chat", "s1", "relationship", "maya")["sparks"] == 1
        assert "Brass key" in service.context_for_prompt(db, "chat", "s1")
    finally:
        db.close()
