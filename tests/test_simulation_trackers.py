import sqlite3
import time

import pytest

from bridge.schema import initialize_database_schema
from bridge.simulation_service import SimulationService


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
    db.commit()
    return db


def _assistant_row(db, text="Story beat."):
    rowid = db.execute(
        "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
        ("chat", "s1", "assistant", text, time.time()),
    ).lastrowid
    db.commit()
    return int(rowid)


def test_relationship_engine_caps_per_turn_and_converts_sparks():
    db = _db()
    service = SimulationService()
    try:
        for _ in range(5):
            rowid = _assistant_row(db)
            service.apply_payload(
                db,
                "chat",
                "s1",
                {"relationships": [{"npc": "Maya", "sparks_delta": 9}]},
                source_rowid=rowid,
            )
        state = service.state(db, "chat", "s1", "relationship", "maya")
        assert state["bond"] == 1
        assert state["sparks"] == 0
        assert state["grudge"] == 0
    finally:
        db.close()


def test_relationship_engine_rejects_positive_direct_bond_and_applies_grudge_cycle():
    db = _db()
    service = SimulationService()
    try:
        row1 = _assistant_row(db)
        with pytest.raises(ValueError, match="positive BOND"):
            service.apply_payload(
                db,
                "chat",
                "s1",
                {"relationships": [{"npc": "Maya", "bond_delta": 1}]},
                source_rowid=row1,
            )
        service.apply_payload(
            db,
            "chat",
            "s1",
            {"relationships": [{"npc": "Maya", "bond_delta": -2, "grudge_delta": 8}]},
            source_rowid=row1,
        )
        for _ in range(2):
            rowid = _assistant_row(db)
            service.apply_payload(db, "chat", "s1", {}, source_rowid=rowid)
        state = service.state(db, "chat", "s1", "relationship", "maya")
        assert state == {
            "display_name": "Maya",
            "bond": -2,
            "sparks": 0,
            "grudge": 0,
        }
    finally:
        db.close()


def test_agenda_ticks_only_offscreen_and_completes_at_max():
    db = _db()
    service = SimulationService()
    try:
        row1 = _assistant_row(db)
        service.apply_payload(
            db,
            "chat",
            "s1",
            {
                "agendas": [
                    {
                        "npc": "Maya",
                        "objective": "Research the archive",
                        "step": 1,
                        "max_steps": 3,
                        "location": "Library",
                    }
                ],
                "on_screen_npcs": ["Maya"],
            },
            source_rowid=row1,
        )
        row2 = _assistant_row(db)
        service.apply_payload(db, "chat", "s1", {"on_screen_npcs": ["Maya"]}, source_rowid=row2)
        assert service.state(db, "chat", "s1", "agenda", "maya")["step"] == 1
        row3 = _assistant_row(db)
        service.apply_payload(db, "chat", "s1", {"on_screen_npcs": []}, source_rowid=row3)
        row4 = _assistant_row(db)
        service.apply_payload(db, "chat", "s1", {"on_screen_npcs": []}, source_rowid=row4)
        agenda = service.state(db, "chat", "s1", "agenda", "maya")
        assert agenda["step"] == 3
        assert agenda["status"] == "completed"
    finally:
        db.close()


def test_actor_faction_quest_and_foreshadowing_are_canonical_prompt_state():
    db = _db()
    service = SimulationService()
    try:
        rowid = _assistant_row(db)
        service.apply_payload(
            db,
            "chat",
            "s1",
            {
                "actor": {
                    "inventory_add": [{"name": "Brass key", "domain": "utility", "modifier": 1}],
                    "skills_add": [{"name": "Charmer", "domain": "social", "modifier": 2}],
                    "conditions_add": [{"name": "Tired", "domain": "physical", "modifier": -1}],
                },
                "factions": [
                    {
                        "name": "City Guard",
                        "goal": "Keep order",
                        "intel": "Gate closes at dusk",
                        "morale": "steady",
                        "conflict": "",
                        "relations": {"Rebels": "hostile"},
                    }
                ],
                "quests": [
                    {
                        "id": "main-gate",
                        "kind": "main",
                        "status": "active",
                        "objective": "Open the sealed gate",
                        "progress_current": 1,
                        "progress_target": 3,
                        "reward": "Safe passage",
                    }
                ],
                "foreshadowing": [
                    {
                        "id": "red-key",
                        "status": "planted",
                        "seed": "A red key bears the old crest",
                        "payoff": "",
                    }
                ],
            },
            source_rowid=rowid,
        )
        context = service.context_for_prompt(db, "chat", "s1")
        assert "Brass key" in context
        assert "Charmer" in context
        assert "City Guard" in context
        assert "Open the sealed gate" in context
        assert "A red key bears the old crest" in context
    finally:
        db.close()


def test_rollback_restores_prior_tracker_state_and_removes_future_entities():
    db = _db()
    service = SimulationService()
    try:
        row1 = _assistant_row(db)
        service.apply_payload(
            db,
            "chat",
            "s1",
            {
                "relationships": [{"npc": "Maya", "sparks_delta": 2}],
                "quests": [
                    {
                        "id": "gate",
                        "kind": "main",
                        "status": "active",
                        "objective": "Open gate",
                        "progress_current": 0,
                        "progress_target": 2,
                        "reward": "",
                    }
                ],
            },
            source_rowid=row1,
        )
        row2 = _assistant_row(db)
        service.apply_payload(
            db,
            "chat",
            "s1",
            {
                "relationships": [{"npc": "Maya", "bond_delta": -1}],
                "quests": [
                    {
                        "id": "gate",
                        "kind": "main",
                        "status": "completed",
                        "objective": "Open gate",
                        "progress_current": 2,
                        "progress_target": 2,
                        "reward": "",
                    }
                ],
                "factions": [{"name": "Watch", "goal": "Patrol", "morale": "high"}],
            },
            source_rowid=row2,
        )
        service.rollback_from_row(db, "chat", "s1", row2)
        assert service.state(db, "chat", "s1", "relationship", "maya")["bond"] == 0
        assert service.state(db, "chat", "s1", "quest", "gate")["status"] == "active"
        assert service.state(db, "chat", "s1", "faction", "watch") is None
    finally:
        db.close()


def test_check_engine_is_idempotent_and_uses_actor_domain_modifiers():
    db = _db()
    service = SimulationService()
    try:
        rowid = _assistant_row(db)
        service.apply_payload(
            db,
            "chat",
            "s1",
            {
                "actor": {
                    "skills_add": [{"name": "Charmer", "domain": "social", "modifier": 2}],
                    "conditions_add": [{"name": "Confident", "domain": "social", "modifier": 1}],
                }
            },
            source_rowid=rowid,
        )
        first = service.perform_check(
            db,
            "chat",
            "s1",
            request_key="op:77",
            domain="social",
            actor="User",
            action="Persuade the guard",
            dc=12,
            roll=10,
        )
        second = service.perform_check(
            db,
            "chat",
            "s1",
            request_key="op:77",
            domain="social",
            actor="User",
            action="different ignored text",
            dc=20,
            roll=1,
        )
        assert first == second
        assert first["modifier"] == 3
        assert first["delta"] == 1
        assert first["outcome"] == "success"
    finally:
        db.close()


def test_check_outcome_tiers_match_internal_state_rules():
    service = SimulationService()
    assert service.classify_check(20, 20, -20) == "critical_success"
    assert service.classify_check(10, 10, 0) == "success"
    assert service.classify_check(9, 10, 0) == "near_miss"
    assert service.classify_check(5, 10, 0) == "failure"
    assert service.classify_check(1, 1, 20) == "critical_failure"


def test_purge_session_removes_all_tracker_state_and_checks():
    db = _db()
    service = SimulationService()
    try:
        rowid = _assistant_row(db)
        service.apply_payload(
            db,
            "chat",
            "s1",
            {"relationships": [{"npc": "Maya", "sparks_delta": 1}]},
            source_rowid=rowid,
        )
        service.perform_check(
            db,
            "chat",
            "s1",
            request_key="op:1",
            domain="social",
            actor="User",
            action="Ask",
            dc=5,
            roll=10,
        )
        service.purge_session(db, "chat", "s1")
        assert service.context_for_prompt(db, "chat", "s1") == ""
        assert db.execute("SELECT COUNT(*) FROM simulation_checks").fetchone()[0] == 0
    finally:
        db.close()
