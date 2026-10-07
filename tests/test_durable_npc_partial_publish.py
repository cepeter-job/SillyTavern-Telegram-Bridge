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
        == "work_failed"
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
    assert job[1] == "work_failed"
