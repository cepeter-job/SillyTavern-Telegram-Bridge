"""Durable NPC worker publishes independently validated tracker state on NPC parse failure."""

import json

from application_test_setup import make_test_provider_port
from test_durable_memory_workers import add
from test_memory_completion_safety import session_db as session_db

from bridge.memory_store import claim_jobs


def test_durable_npc_worker_publishes_valid_tracker_when_npc_output_is_malformed(session_db):
    from bridge.memory_workers import run_memory_claim
    from bridge.npc_repository import get_npc_extraction_coverage
    from bridge.simulation_service import SimulationService

    settings, db, session = session_db
    add(db, "The user receives a raincoat.")
    claim = claim_jobs(db, layers=("npc",))[0]
    provider = make_test_provider_port(
        generate_backend=lambda *a, **k: json.dumps(
            {
                "npcs": "malformed",
                "simulation": {"actor": {"inventory_add": ["Raincoat"]}},
            }
        )
    )

    assert (
        run_memory_claim(db, claim, session, {"name": "Alice"}, provider_port=provider, app_settings=settings)
        == "invalid_npc_output"
    )
    actor = SimulationService().state(db, "chat", "s1", "actor", "user")
    assert actor is not None
    assert actor["inventory"][0]["name"] == "Raincoat"
    assert get_npc_extraction_coverage(db, "chat", "s1") == 0
    assert (
        db.execute(
            "SELECT MAX(source_rowid) FROM simulation_sources WHERE chat_id=? AND session_id=?",
            ("chat", "s1"),
        ).fetchone()[0]
        == 1
    )
    job = db.execute(
        "SELECT completed_version,last_error FROM memory_jobs WHERE chat_id=? AND session_id=? AND layer='npc'",
        ("chat", "s1"),
    ).fetchone()
    assert job[0] == 0
    assert job[1] == "invalid_npc_output"


def test_failed_npc_root_repair_preserves_already_valid_simulation(session_db):
    from bridge.memory_workers import run_memory_claim
    from bridge.npc_repository import get_npc_extraction_coverage
    from bridge.simulation_service import SimulationService

    settings, db, session = session_db
    add(db, "The user receives a raincoat.")
    calls = []

    def generate(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return json.dumps({"npcs": "malformed", "simulation": {"actor": {"inventory_add": ["Raincoat"]}}})
        raise TimeoutError("synthetic repair failure")

    result = run_memory_claim(
        db,
        claim_jobs(db, layers=("npc",))[0],
        session,
        {"name": "Alice"},
        provider_port=make_test_provider_port(generate_backend=generate),
        app_settings=settings,
    )
    assert result == "invalid_npc_output"
    assert len(calls) == 2
    actor = SimulationService().state(db, "chat", "s1", "actor", "user")
    assert actor is not None and actor["inventory"][0]["name"] == "Raincoat"
    assert get_npc_extraction_coverage(db, "chat", "s1") == 0
    assert db.execute("SELECT completed_version FROM memory_jobs WHERE layer='npc'").fetchone() == (0,)


def test_successful_npc_root_repair_does_not_erase_valid_original_simulation(session_db):
    from bridge.memory_workers import run_memory_claim
    from bridge.npc_repository import get_npc_extraction_coverage
    from bridge.simulation_service import SimulationService

    settings, db, session = session_db
    add(db, "The user receives a raincoat.")
    responses = iter(
        [
            json.dumps({"npcs": "malformed", "simulation": {"actor": {"inventory_add": ["Raincoat"]}}}),
            '{"npcs":[],"simulation":{}}',
        ]
    )
    result = run_memory_claim(
        db,
        claim_jobs(db, layers=("npc",))[0],
        session,
        {"name": "Alice"},
        provider_port=make_test_provider_port(generate_backend=lambda *a, **k: next(responses)),
        app_settings=settings,
    )
    assert result == "complete"
    actor = SimulationService().state(db, "chat", "s1", "actor", "user")
    assert actor is not None and actor["inventory"][0]["name"] == "Raincoat"
    assert get_npc_extraction_coverage(db, "chat", "s1") == 1
