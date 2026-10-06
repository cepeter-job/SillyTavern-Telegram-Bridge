"""Canonical saved-state fixtures shared by tracker route and DOM tests."""

import time

from bridge.narrative_arc_repository import store_arc_row
from bridge.narrative_repository import upsert_narrative_state_if_fresh
from bridge.npc_service import NpcService
from bridge.npc_types import NpcExtractionGroup, NpcOperation
from bridge.simulation_service import SimulationService
from bridge.sqlite_store import write_transaction


def seed_trackers(db, chat_id, session_id):
    with write_transaction(db):
        source = db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,'assistant',?,?)",
            (chat_id, session_id, "Maya gives you a key while Rowan travels to the tower.", time.time()),
        ).lastrowid
    for name, field, value in [
        ("Maya <guide>", "role", "Guide"),
        ("Rowan", "agenda", "Reach the tower"),
    ]:
        NpcService().apply_group(
            db,
            chat_id,
            session_id,
            NpcExtractionGroup(name, (), (NpcOperation(field, "set", value, "mutable", "shared", ()),)),
            source_rowid=source,
            primary_name="Alice",
            user_name="User",
        )
    with write_transaction(db):
        store_arc_row(
            db,
            chat_id,
            session_id,
            {
                "arc_id": "watchtower",
                "title": "Watchtower",
                "status": "active",
                "phase": "setup",
                "importance": "major",
                "summary": "The watchtower remains closed",
                "open_questions": [],
                "related_threads": [],
                "source_revision": source,
                "evidence": [],
            },
        )
        revision = db.execute(
            "SELECT state_revision FROM narrative_state WHERE chat_id=? AND session_id=?", (chat_id, session_id)
        ).fetchone()[0]
        upsert_narrative_state_if_fresh(db, chat_id, session_id, "{}", revision, source, 1)
    service = SimulationService()
    service.apply_payload(
        db,
        chat_id,
        session_id,
        {
            "relationships": [{"npc": "Maya <guide>", "bond_delta": -1, "sparks_delta": 2, "grudge_delta": 1}],
            "agendas": [
                {"npc": "Maya <guide>", "objective": "Secret tunnel", "max_steps": 4},
                {
                    "npc": "Rowan",
                    "objective": "Reach <b>the tower</b>",
                    "step": 1,
                    "max_steps": 3,
                    "location": "North ridge",
                },
            ],
            "actor": {
                "inventory_add": [{"name": "Brass key", "domain": "stealth", "modifier": 1}],
                "skills_add": [{"name": "Persuasion", "domain": "social", "modifier": 2}],
                "conditions_add": [{"name": "Tired", "domain": "any", "modifier": -1}],
            },
            "factions": [
                {
                    "name": "Harbor Watch",
                    "goal": "Protect the docks",
                    "morale": "Steady",
                    "conflict": "Smugglers",
                    "intel": "Secret vault",
                    "lies": ["Secret cover"],
                    "relations": {"Cabal": "Secret allies"},
                }
            ],
            "quests": [
                {
                    "id": "Open gate",
                    "kind": "side",
                    "status": "active",
                    "objective": "Find the gate",
                    "progress_current": 1,
                    "progress_target": 3,
                    "reward": "Passage",
                },
                {
                    "id": "Watchtower route",
                    "arc_id": "watchtower",
                    "status": "completed",
                    "objective": "Reach the watchtower",
                    "progress_current": 2,
                    "progress_target": 4,
                },
            ],
            "foreshadowing": [{"id": "Heir", "seed": "Secret heir", "payoff": "Secret betrayal"}],
        },
        source_rowid=source,
    )
    service.perform_check(
        db,
        chat_id,
        session_id,
        request_key="private-operation-key",
        domain="stealth",
        actor="user",
        action="Open the locked door",
        dc=10,
        roll=12,
        modifier=1,
        source_rowid=source,
    )
    return source
