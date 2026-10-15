"""Regression contracts: structured helpers must not request prose continuations."""

import pytest
from application_test_setup import make_test_provider_port
from memory_runtime_test_support import isolated_memory_runtime as isolated_memory_runtime
from test_durable_memory_workers import add
from test_memory_completion_safety import session_db as session_db

from bridge.memory_store import claim_jobs
from bridge.memory_workers import run_memory_claim


@pytest.mark.parametrize(
    "layer,response,root_key",
    [
        ("episodes", '{"memories":[],"no_memory_reason":"Only quiet transient scene detail was present."}', "memories"),
        ("npc", '{"npcs":[],"simulation":{}}', "npcs"),
        ("curator", '{"memories":[]}', "memories"),
        ("scene", '{"state":{},"blocks":[]}', "state"),
    ],
)
def test_structured_helpers_request_one_complete_object(session_db, layer, response, root_key):
    settings, db, session = session_db
    add(db, "The room is quiet. No new supporting character is present.")
    calls = []

    def generate(*args, **kwargs):
        assert not db.in_transaction
        calls.append((args, kwargs))
        return response

    claim = claim_jobs(db, layers=(layer,))[0]
    result = run_memory_claim(
        db,
        claim,
        session,
        {"name": "Alice"},
        provider_port=make_test_provider_port(generate_backend=generate),
        app_settings=settings,
    )
    assert result == "complete"
    assert len(calls) == 1
    args, options = calls[0]
    assert options["settings"].get("json_once") is True
    assert options["force_non_stream"] is True
    instruction = "\n".join(m["content"] for m in args[2] if m["role"] == "system")
    assert '"' + root_key + '"' in instruction
    assert "Return only a JSON array" not in instruction
    assert db.execute("SELECT dirty_version=completed_version FROM memory_jobs WHERE layer=?", (layer,)).fetchone() == (
        1,
    )


def test_empty_episode_object_cannot_be_silently_accepted_as_no_memories(session_db):
    settings, db, session = session_db
    add(db, "Alice promised to return the sealed letter tomorrow.")
    seen = []

    def generate(*args, **kwargs):
        seen.append(1)
        return "{}"

    result = run_memory_claim(
        db,
        claim_jobs(db, layers=("episodes",))[0],
        session,
        {"name": "Alice"},
        provider_port=make_test_provider_port(generate_backend=generate),
        app_settings=settings,
    )
    assert result == "invalid_shape"
    assert len(seen) == 2
    assert db.execute("SELECT completed_version FROM memory_jobs WHERE layer='episodes'").fetchone() == (0,)


def test_missing_npc_root_repairs_npcs_not_only_simulation(session_db):
    settings, db, session = session_db
    add(db, "Mira is an archivist. No item changed hands.")
    repaired = (
        '{"npcs":[{"name":"Mira","aliases":[],"operations":[{"field":"role",'
        '"op":"set","value":"Archivist","mode":"mutable","visibility":"shared",'
        '"known_by":[]}]}],"simulation":{}}'
    )
    responses = iter(["{}", repaired])
    calls = []

    def generate(*args, **kwargs):
        calls.append((args, kwargs))
        return next(responses)

    result = run_memory_claim(
        db,
        claim_jobs(db, layers=("npc",))[0],
        session,
        {"name": "Alice"},
        provider_port=make_test_provider_port(generate_backend=generate),
        app_settings=settings,
    )
    assert result == "complete"
    assert len(calls) == 2
    assert calls[0][0][2][-1] == calls[1][0][2][-1]
    repair = "\n".join(m["content"] for m in calls[1][0][2] if m["role"] == "system")
    assert '"npcs"' in repair and '"simulation"' in repair
    assert "no NPC data" not in repair
    assert all(x[1]["settings"].get("json_once") is True for x in calls)
    assert db.execute("SELECT COUNT(*) FROM npc_entities WHERE display_name=?", ("Mira",)).fetchone() == (1,)


def test_npc_root_repair_failure_remains_bounded_and_does_not_complete(session_db):
    settings, db, session = session_db
    add(db, "Mira still holds the key; Alice did not receive it.")
    calls = []

    def generate(*args, **kwargs):
        calls.append(1)
        return "{}"

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
    assert db.execute("SELECT completed_version FROM memory_jobs WHERE layer='npc'").fetchone() == (0,)
