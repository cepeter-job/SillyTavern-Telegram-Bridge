"""Real worker contracts over synthetic SQLite and fake provider responses."""

import json

import pytest
from application_test_setup import make_test_provider_port
from memory_runtime_test_support import isolated_memory_runtime as isolated_memory_runtime
from test_durable_memory_workers import add
from test_memory_completion_safety import session_db as session_db

from bridge.memory_store import claim_jobs
from bridge.memory_workers import run_memory_claim


@pytest.mark.parametrize(
    "layer,valid",
    [
        ("summary", '{"blocks":[{"text":"Established event","visibility":"shared","known_by":[]}]}'),
        ("scene", '{"state":{},"blocks":[]}'),
        ("episodes", "[]"),
    ],
)
def test_malformed_json_repairs_once_then_completes(session_db, layer, valid):
    settings, db, session = session_db
    add(db)
    calls = []
    responses = iter(['{"broken":', valid])

    def generate(*args, **kwargs):
        assert not db.in_transaction
        calls.append((args, kwargs))
        return next(responses)

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
    assert len(calls) == 2
    assert calls[0][0][2][-1] == calls[1][0][2][-1], "repair keeps exact canonical source"
    assert calls[1][1]["settings"]["stop_sequences"] == ""
    assert db.execute("SELECT dirty_version=completed_version FROM memory_jobs WHERE layer=?", (layer,)).fetchone() == (
        1,
    )


def test_failed_repair_is_bounded_classified_and_private(session_db, caplog):
    settings, db, session = session_db
    add(db, "PRIVATE_TRANSCRIPT_CANARY")
    calls = []

    def generate(*args, **kwargs):
        calls.append(1)
        return '{"PRIVATE_RESPONSE_CANARY":'

    claim = claim_jobs(db, layers=("summary",))[0]
    result = run_memory_claim(
        db,
        claim,
        session,
        {"name": "Alice"},
        provider_port=make_test_provider_port(generate_backend=generate),
        app_settings=settings,
    )
    assert result == "malformed_json"
    assert len(calls) == 2
    row = db.execute(
        "SELECT last_error,completed_version,lease_token FROM memory_jobs WHERE layer='summary'"
    ).fetchone()
    assert row == ("malformed_json", 0, "")
    assert "scope=" in caplog.text and "target=" in caplog.text
    assert "PRIVATE_TRANSCRIPT_CANARY" not in caplog.text
    assert "PRIVATE_RESPONSE_CANARY" not in caplog.text


def test_conflicting_audience_is_not_repaired_or_published(session_db, caplog):
    settings, db, session = session_db
    add(db)
    calls = []

    def generate(*args, **kwargs):
        calls.append(1)
        return json.dumps({"state": {}, "blocks": [{"text": "secret", "visibility": "shared", "known_by": ["Alice"]}]})

    claim = claim_jobs(db, layers=("scene",))[0]
    result = run_memory_claim(
        db,
        claim,
        session,
        {"name": "Alice"},
        provider_port=make_test_provider_port(generate_backend=generate),
        app_settings=settings,
    )
    assert result == "invalid_audience"
    assert len(calls) == 1
    assert db.execute("SELECT completed_version FROM memory_jobs WHERE layer='scene'").fetchone() == (0,)
    assert "secret" not in caplog.text


@pytest.mark.parametrize(
    "layer,valid",
    [
        ("summary", '{"blocks":[{"text":"Established event","visibility":"shared","known_by":[]}]}'),
        ("scene", '{"state":{},"blocks":[]}'),
        ("episodes", "[]"),
    ],
)
def test_claim_replaced_during_repair_never_publishes(session_db, layer, valid):
    settings, db, session = session_db
    add(db)
    calls = []

    def generate(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return "broken"
        db.execute("UPDATE memory_jobs SET lease_token='replacement' WHERE layer=?", (layer,))
        db.commit()
        return valid

    claim = claim_jobs(db, layers=(layer,))[0]
    result = run_memory_claim(
        db,
        claim,
        session,
        {"name": "Alice"},
        provider_port=make_test_provider_port(generate_backend=generate),
        app_settings=settings,
    )
    assert result == "stale_source"
    assert db.execute("SELECT covered_id FROM memory_layer_state WHERE layer=?", (layer,)).fetchone() == (0,)
    assert db.execute("SELECT lease_token,completed_version FROM memory_jobs WHERE layer=?", (layer,)).fetchone() == (
        "replacement",
        0,
    )


@pytest.mark.parametrize(
    "layer,payload",
    [
        ("scene", {"state": [], "blocks": [{"text": "secret", "visibility": "shared", "known_by": ["Alice"]}]}),
        ("summary", {"blocks": [{"text": "", "visibility": "shared", "known_by": ["Alice"]}]}),
    ],
)
def test_mixed_schema_and_audience_failure_never_regenerates(session_db, layer, payload):
    settings, db, session = session_db
    add(db)
    calls = []

    def generate(*args, **kwargs):
        calls.append(1)
        return json.dumps(payload)

    claim = claim_jobs(db, layers=(layer,))[0]
    result = run_memory_claim(
        db,
        claim,
        session,
        {"name": "Alice"},
        provider_port=make_test_provider_port(generate_backend=generate),
        app_settings=settings,
    )
    assert result == "invalid_audience"
    assert calls == [1]
    assert db.execute("SELECT covered_id FROM memory_layer_state WHERE layer=?", (layer,)).fetchone() == (0,)


def test_unknown_provider_exception_stays_generic_without_secret_log(session_db, caplog):
    settings, db, session = session_db
    add(db)

    def generate(*args, **kwargs):
        raise RuntimeError("PRIVATE_PROVIDER_EXCEPTION_CANARY")

    claim = claim_jobs(db, layers=("summary",))[0]
    result = run_memory_claim(
        db,
        claim,
        session,
        {"name": "Alice"},
        provider_port=make_test_provider_port(generate_backend=generate),
        app_settings=settings,
    )
    assert result == "work_failed"
    assert "PRIVATE_PROVIDER_EXCEPTION_CANARY" not in caplog.text
    assert "code=work_failed" in caplog.text


@pytest.mark.parametrize("code", ["malformed_json", "invalid_shape", "invalid_audience", "invalid_npc_output"])
def test_classified_failure_does_not_bypass_autonomous_retry_limit(session_db, code):
    from bridge.memory_queue import AUTO_FAILURE_LIMIT

    _, db, _ = session_db
    add(db)
    db.execute(
        "UPDATE memory_jobs SET attempts=?,last_error=?,next_attempt_at=0 WHERE layer='summary'",
        (AUTO_FAILURE_LIMIT, code),
    )
    db.commit()
    assert claim_jobs(db, layers=("summary",), autonomous=True) == []
