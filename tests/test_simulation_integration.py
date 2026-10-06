"""The existing NPC worker owns atomic tracker extraction and source proof."""

import json

import pytest
from application_test_setup import make_test_provider_port
from test_memory_completion_safety import session_db as session_db
from test_simulation_trackers import _assistant_row

from bridge.npc_extraction import refresh_npc_state_now
from bridge.npc_repository import get_npc_extraction_coverage
from bridge.simulation_service import SimulationService


def refresh(case, generate):
    config, db, session = case
    return refresh_npc_state_now(
        db,
        "chat",
        session,
        {"name": "Alice"},
        provider_port=make_test_provider_port(generate_backend=generate),
        app_settings=config,
    )


def test_existing_npc_utility_call_publishes_simulation_without_a_second_model_call(session_db):
    _, db, _ = session_db
    source = _assistant_row(db, "The user receives a brass key.")
    seen = []

    def generate(_key, _model, messages, **kwargs):
        seen.append(messages)
        assert not db.in_transaction
        return json.dumps({"npcs": [], "simulation": {"actor": {"inventory_add": ["Brass key"]}}})

    refresh(session_db, generate)
    assert len(seen) == 1
    assert "simulation" in seen[0][0]["content"] and "bridge-owned" in seen[0][0]["content"]
    assert SimulationService().state(db, "chat", "s1", "actor", "user")["inventory"][0]["name"] == "Brass key"
    assert get_npc_extraction_coverage(db, "chat", "s1") == source


def test_malformed_simulation_does_not_advance_coverage(session_db):
    _, db, _ = session_db
    _assistant_row(db)
    refresh(session_db, lambda *a, **k: '{"npcs":[],"simulation":{"relationships":[{"npc":"Maya","apology":"false"}]}}')
    assert get_npc_extraction_coverage(db, "chat", "s1") == 0
    assert db.execute("SELECT COUNT(*) FROM simulation_sources").fetchone()[0] == 0


def test_parts_remain_private_until_the_complete_source_row(session_db):
    _, db, _ = session_db
    _assistant_row(db, "Long story. " * 1500)
    calls = []

    def generate(*args, **kwargs):
        assert SimulationService().state(db, "chat", "s1", "actor", "user") is None
        calls.append(1)
        operation = "add" if len(calls) == 1 else "remove"
        return json.dumps({"npcs": [], "simulation": {"actor": {f"inventory_{operation}": ["Transient key"]}}})

    refresh(session_db, generate)
    assert len(calls) == 2
    assert SimulationService().state(db, "chat", "s1", "actor", "user")["inventory"] == []


@pytest.mark.parametrize(
    "quote,expected", [("Maya: BOND=8 Sparks=3 Grudge=2", 8), ("Maya: BOND=20 Sparks=3 Grudge=2", None)]
)
def test_legacy_baseline_requires_exact_numeric_source_evidence(session_db, quote, expected):
    _, db, _ = session_db
    _assistant_row(db, "Story.<internal_states>Maya: BOND=8 Sparks=3 Grudge=2</internal_states>")
    refresh(
        session_db,
        lambda *a, **k: json.dumps(
            {
                "npcs": [],
                "simulation": {
                    "relationships": [
                        {"npc": "Maya", "baseline": {"bond": expected or 20, "sparks": 3, "grudge": 2, "quote": quote}}
                    ]
                },
            }
        ),
    )
    state = SimulationService().state(db, "chat", "s1", "relationship", "maya")
    assert (state["bond"] if state else None) == expected


def test_director_and_choices_use_their_captured_tracker_boundary(session_db):
    from types import SimpleNamespace

    from bridge.director_prompt import build_director_input
    from bridge.director_repository import load_director_state
    from bridge.light_novel_service import build_choice_context_snapshot
    from bridge.narrative_values import NarrativeSettings, NarrativeState

    config, db, session = session_db
    first = _assistant_row(db)
    SimulationService().apply_payload(db, "chat", "s1", {"actor": {"inventory_add": ["Past key"]}}, source_rowid=first)
    later = _assistant_row(db)
    SimulationService().apply_payload(
        db, "chat", "s1", {"actor": {"inventory_add": ["Future map"]}}, source_rowid=later
    )
    choice = build_choice_context_snapshot(
        db,
        SimpleNamespace(chat_id="chat", session_id="s1", assistant_rowid=first),
        session,
        {"name": "Alice"},
        "Story.",
        app_settings=config,
    )
    director, *_ = build_director_input(
        db,
        "chat",
        session,
        NarrativeState(updated_through_rowid=first),
        NarrativeSettings(),
        load_director_state(db, "chat", "s1"),
        app_settings=config,
    )
    for payload in (choice, director):
        assert "Past key" in payload["simulation_state"] and "Future map" not in payload["simulation_state"]


@pytest.mark.parametrize("rewrite", [False, True])
def test_upgrade_replays_tracker_sources_without_overwriting_existing_npc_fields(tmp_path, monkeypatch, rewrite):
    from settings_test_support import make_test_settings
    from test_npc_service import _group, _op
    from test_simulation_trackers import _db

    from bridge import schema
    from bridge.memory_store import enqueue_memory
    from bridge.npc_repository import find_npc_by_name_or_alias, load_npc_fields, set_npc_extraction_coverage
    from bridge.npc_service import NpcService
    from bridge.sqlite_store import write_transaction

    with monkeypatch.context() as old:
        old.setattr(schema, "SCHEMA_MIGRATIONS", schema.SCHEMA_MIGRATIONS[:-1])
        db = _db()
    try:
        source = _assistant_row(db, "Story.<internal_states>Maya: BOND=8 Sparks=3 Grudge=2</internal_states>")
        NpcService().apply_group(
            db,
            "chat",
            "s1",
            _group(name="Maya", operations=[_op("role", "Manual current role")]),
            source_rowid=source,
            primary_name="Alice",
            user_name="User",
        )
        enqueue_memory(db, "chat", "s1", "npc")
        with write_transaction(db):
            set_npc_extraction_coverage(db, "chat", "s1", source, 1)
            db.execute("UPDATE memory_layer_state SET covered_id=? WHERE layer='npc'", (source,))
        schema.initialize_database_schema(db)
        assert get_npc_extraction_coverage(db, "chat", "s1") == 0
        if rewrite:
            with write_transaction(db):
                NpcService().rollback_from_row(db, "chat", "s1", source)
                SimulationService().rollback_from_row(db, "chat", "s1", source)
                db.execute("UPDATE messages SET content='Maya is now a doctor.' WHERE id=?", (source,))
        payload = {
            "npcs": [
                {
                    "name": "Maya",
                    "aliases": [],
                    "operations": [
                        {
                            "field": "role",
                            "op": "set",
                            "value": "Doctor" if rewrite else "Older extracted role",
                            "mode": "mutable",
                            "visibility": "shared",
                            "known_by": [],
                        }
                    ],
                }
            ],
            "simulation": {
                "relationships": [
                    {
                        "npc": "Maya",
                        "baseline": {"bond": 8, "sparks": 3, "grudge": 2, "quote": "Maya: BOND=8 Sparks=3 Grudge=2"},
                    }
                ]
            },
        }
        if rewrite:
            payload["simulation"] = {}
        refresh_npc_state_now(
            db,
            "chat",
            {"session_id": "s1", "model_id": "p::m", "persona_id": ""},
            {"name": "Alice"},
            provider_port=make_test_provider_port(generate_backend=lambda *a, **k: json.dumps(payload)),
            app_settings=make_test_settings(home=tmp_path),
        )
        entity = find_npc_by_name_or_alias(db, "chat", "s1", "Maya")
        assert entity is not None
        assert load_npc_fields(db, entity.npc_id)["role"].value == ("Doctor" if rewrite else "Manual current role")
        if not rewrite:
            assert SimulationService().state(db, "chat", "s1", "relationship", "Maya")["bond"] == 8
    finally:
        db.close()
