"""Canonical NPC identities adopt earlier trackers without rewriting history."""

import json

from test_memory_completion_safety import session_db as session_db
from test_npc_service import _group, _op
from test_simulation_integration import refresh
from test_simulation_projection import npc
from test_simulation_trackers import _assistant_row

from bridge.npc_repository import get_npc_extraction_coverage, list_npc_entities, load_npc_fields
from bridge.npc_service import NpcService
from bridge.simulation_repository import load_states_as_of, store_state
from bridge.simulation_service import SimulationService
from bridge.sqlite_store import write_transaction


def test_delayed_alias_adopts_prior_scores_and_agenda_with_historical_rollback(session_db):
    _, db, _ = session_db
    service = SimulationService()
    first = _assistant_row(db)
    service.apply_payload(
        db,
        "chat",
        "s1",
        {
            "relationships": [{"npc": "Maya", "sparks_delta": 2}],
            "agendas": [{"npc": "Maya", "objective": "Research", "step": 2, "max_steps": 5}],
        },
        source_rowid=first,
    )
    before = {kind: service.state(db, "chat", "s1", kind, "maya") for kind in ("relationship", "agenda")}
    second = _assistant_row(db)
    entity = npc(db, second)
    service.apply_payload(
        db, "chat", "s1", {"relationships": [{"npc": "Maya", "sparks_delta": 1}]}, source_rowid=second
    )
    assert service.state(db, "chat", "s1", "relationship", "maya") is None
    assert service.state(db, "chat", "s1", "relationship", "maya torres")["sparks"] == 3
    assert service.state(db, "chat", "s1", "agenda", "maya") is None
    assert service.state(db, "chat", "s1", "agenda", "maya torres")["step"] == 3
    context = service.context_for_prompt(db, "chat", "s1")
    assert context.count("REL ") == context.count("AGENDA ") == 1
    assert "3/5" in load_npc_fields(db, entity.npc_id)["agenda"].value
    for kind in before:
        assert service.state(db, "chat", "s1", kind, "maya", through_rowid=first) == before[kind]
        assert service.state(db, "chat", "s1", kind, "maya torres", through_rowid=first) is None
    service.rollback_from_row(db, "chat", "s1", second)
    for kind in before:
        assert service.state(db, "chat", "s1", kind, "maya") == before[kind]
        assert service.state(db, "chat", "s1", kind, "maya torres") is None


def test_conflicting_alias_state_rejects_complete_worker_publication(session_db):
    _, db, _ = session_db
    first = _assistant_row(db)
    refresh(
        session_db,
        lambda *a, **k: json.dumps(
            {
                "npcs": [],
                "simulation": {
                    "relationships": [{"npc": "Maya", "sparks_delta": 2}, {"npc": "Maya Torres", "sparks_delta": 1}]
                },
            }
        ),
    )
    second = _assistant_row(db)
    refresh(
        session_db,
        lambda *a, **k: json.dumps(
            {
                "npcs": [
                    {
                        "name": "Maya Torres",
                        "aliases": ["Maya"],
                        "operations": [
                            {"field": "role", "op": "set", "value": "Guard", "mode": "mutable", "visibility": "shared"}
                        ],
                    }
                ],
                "simulation": {},
            }
        ),
    )
    assert get_npc_extraction_coverage(db, "chat", "s1") == first
    assert list_npc_entities(db, "chat", "s1") == []
    assert db.execute("SELECT source_rowid FROM simulation_sources").fetchall() == [(first,)]
    assert second > first


def test_identical_alias_values_coalesce_and_native_manual_field_remains_owned(session_db):
    _, db, _ = session_db
    first = _assistant_row(db)
    service = SimulationService()
    service.apply_payload(
        db,
        "chat",
        "s1",
        {"relationships": [{"npc": "Maya", "sparks_delta": 2}, {"npc": "Maya Torres", "sparks_delta": 2}]},
        source_rowid=first,
    )
    second = _assistant_row(db)
    NpcService().apply_group(
        db,
        "chat",
        "s1",
        _group(aliases=["Maya"], operations=[_op("relationship", "Manual attitude")]),
        source_rowid=second,
        primary_name="Alice",
        user_name="User",
    )
    entity = list_npc_entities(db, "chat", "s1")[0]
    field = load_npc_fields(db, entity.npc_id)["relationship"]
    service.apply_payload(db, "chat", "s1", {}, source_rowid=second)
    assert service.state(db, "chat", "s1", "relationship", "maya") is None
    assert service.state(db, "chat", "s1", "relationship", "maya torres")["sparks"] == 2
    assert load_npc_fields(db, entity.npc_id)["relationship"] == field


def test_historical_tombstones_do_not_hide_live_entities_at_the_domain_bound(session_db):
    _, db, _ = session_db
    source = _assistant_row(db)
    with write_transaction(db):
        for number in range(400):
            name = f"former-{number:03}"
            store_state(db, "chat", "s1", "quest", name, {"objective": "Old"}, source_rowid=source, now=1)
            store_state(db, "chat", "s1", "quest", name, None, source_rowid=source, now=1)
        for number in range(64):
            store_state(
                db, "chat", "s1", "quest", f"live-{number:03}", {"objective": "Live"}, source_rowid=source, now=1
            )
    states = load_states_as_of(db, "chat", "s1", through_rowid=source)
    assert len(states) == 64 and all(name.startswith("live-") for _, name, _, _ in states)


def test_backfill_does_not_adopt_or_project_an_identity_established_in_the_future(session_db):
    _, db, _ = session_db
    first = _assistant_row(db)
    second = _assistant_row(db)
    entity = npc(db, second)
    service = SimulationService()
    service.apply_payload(db, "chat", "s1", {"relationships": [{"npc": "Maya", "sparks_delta": 2}]}, source_rowid=first)
    assert service.state(db, "chat", "s1", "relationship", "maya")["sparks"] == 2
    assert service.state(db, "chat", "s1", "relationship", "maya torres") is None
    assert "relationship" not in load_npc_fields(db, entity.npc_id)
    service.apply_payload(db, "chat", "s1", {}, source_rowid=second)
    assert service.state(db, "chat", "s1", "relationship", "maya") is None
    assert service.state(db, "chat", "s1", "relationship", "maya torres")["sparks"] == 2
