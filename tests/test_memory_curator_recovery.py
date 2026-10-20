"""Curator recovery uses canonical input without accepting failed extraction as coverage."""

import json

import pytest
from application_test_setup import make_test_provider_port
from memory_runtime_test_support import isolated_memory_runtime as isolated_memory_runtime
from session_test_support import session_db as session_db
from test_durable_memory_workers import add

from bridge.generation_settings import get_generation_settings, update_generation_settings
from bridge.memory_curator import get_curated_memory_state, parse_curator_response
from bridge.memory_queue import AUTO_FAILURE_LIMIT
from bridge.memory_store import claim_jobs
from bridge.memory_workers import run_memory_claim
from bridge.provider_completion import StructuredOutputCapped

VALID = '{"memories":[{"key":"fixture.event","text":"Established durable event.","kind":"event"}]}'


def run_curator(settings, db, session, generate):
    claim = claim_jobs(db, layers=("curator",))[0]
    return run_memory_claim(
        db,
        claim,
        session,
        {"name": "Synthetic character"},
        provider_port=make_test_provider_port(generate_backend=generate),
        app_settings=settings,
    )


@pytest.mark.parametrize(
    "raw,code",
    [
        ("", "malformed_json"),
        ('{"PRIVATE_RESPONSE_CANARY":', "malformed_json"),
        ("[]", "invalid_shape"),
        ("{}", "invalid_shape"),
        ('{"memories":{}}', "invalid_shape"),
        ('{"memories":[null,{"text":""}]}', "invalid_shape"),
    ],
    ids=["empty", "malformed", "root", "envelope", "array-type", "all-invalid"],
)
def test_repeated_curator_rejection_is_classified_without_coverage(session_db, caplog, raw, code):
    settings, db, session = session_db
    add(db, "PRIVATE_SOURCE_CANARY")
    calls = []

    def generate(*args, **kwargs):
        assert not db.in_transaction
        calls.append((args, kwargs))
        return raw

    assert run_curator(settings, db, session, generate) == code
    assert len(calls) == 2
    assert get_curated_memory_state(db, "chat", "s1") == ([], 0)
    assert db.execute("SELECT covered_id FROM memory_layer_state WHERE layer='curator'").fetchone() == (0,)
    assert db.execute("SELECT COUNT(*) FROM memory_segments WHERE layer='curator' AND valid=1").fetchone() == (0,)
    assert db.execute(
        "SELECT last_error,completed_version,lease_token FROM memory_jobs WHERE layer='curator'"
    ).fetchone() == (code, 0, "")
    assert "PRIVATE_SOURCE_CANARY" not in caplog.text
    assert "PRIVATE_RESPONSE_CANARY" not in caplog.text


@pytest.mark.parametrize("initial", ["malformed", "shape", "capped"])
def test_curator_repairs_once_from_canonical_input_with_the_same_budget(session_db, initial):
    settings, db, session = session_db
    add(db, "Established durable event.")
    update_generation_settings(db, "chat", "s1", max_tokens=8000, stop_sequences="Story stop")
    calls = []

    def generate(*args, **kwargs):
        assert not db.in_transaction
        calls.append((args, kwargs))
        if len(calls) == 1:
            if initial == "capped":
                raise StructuredOutputCapped('{"memories":[{"text":"PRIVATE_RESPONSE_CANARY"}]}')
            return '{"PRIVATE_RESPONSE_CANARY":' if initial == "malformed" else "{}"
        return VALID

    assert run_curator(settings, db, session, generate) == "complete"
    assert len(calls) == 2
    first, repaired = calls
    assert repaired[0][2][1:] == first[0][2], "repair preserves the exact canonical source and accepted prior state"
    assert "PRIVATE_RESPONSE_CANARY" not in json.dumps(repaired[0][2])
    assert "visibility" not in repaired[0][2][0]["content"]
    assert "known_by" not in repaired[0][2][0]["content"]
    assert first[0][1] == repaired[0][1]
    assert first[1]["settings"] == repaired[1]["settings"]
    assert all(call[1]["settings"]["max_tokens"] == settings.memory_curator_max_tokens for call in calls)
    assert all(call[1]["settings"]["stop_sequences"] == "" for call in calls)
    assert all(call[1]["settings"]["json_once"] is True for call in calls)
    assert all(call[1]["force_non_stream"] is True for call in calls)
    assert get_generation_settings(db, "chat", "s1")["max_tokens"] == 8000
    assert get_generation_settings(db, "chat", "s1")["stop_sequences"] == "Story stop"
    assert get_curated_memory_state(db, "chat", "s1")[0][0]["key"] == "fixture.event"
    assert db.execute("SELECT dirty_version=completed_version FROM memory_jobs WHERE layer='curator'").fetchone() == (
        1,
    )


def test_all_invalid_populated_curator_output_preserves_accepted_accumulator(session_db):
    settings, db, session = session_db
    add(db, "Established durable event.")
    assert run_curator(settings, db, session, lambda *args, **kwargs: VALID) == "complete"
    accepted = get_curated_memory_state(db, "chat", "s1")
    add(db, "A later source part.")
    calls = []

    def generate(*args, **kwargs):
        calls.append((args, kwargs))
        return '{"memories":[null]}'

    assert run_curator(settings, db, session, generate) == "invalid_shape"
    assert len(calls) == 2
    assert get_curated_memory_state(db, "chat", "s1") == accepted
    assert db.execute("SELECT covered_id FROM memory_layer_state WHERE layer='curator'").fetchone() == (accepted[1],)
    assert db.execute("SELECT dirty_version>completed_version FROM memory_jobs WHERE layer='curator'").fetchone() == (
        1,
    )
    assert "Established durable event." in calls[1][0][2][-1]["content"]


@pytest.mark.parametrize("change", ["source", "disabled"])
def test_curator_invalidated_before_repair_does_not_call_again(session_db, change):
    settings, db, session = session_db
    add(db, "Established durable event.")
    calls = []

    def generate(*args, **kwargs):
        calls.append(1)
        if change == "source":
            db.execute("UPDATE messages SET content='Rewritten source' WHERE chat_id='chat' AND session_id='s1'")
        else:
            db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('memory_mode:chat','off')")
        db.commit()
        return "broken JSON"

    assert run_curator(settings, db, session, generate) == "stale_source"
    assert calls == [1]
    assert get_curated_memory_state(db, "chat", "s1") == ([], 0)
    assert db.execute("SELECT covered_id FROM memory_layer_state WHERE layer='curator'").fetchone() == (0,)


def test_curator_repair_invalidated_before_acceptance_does_not_publish(session_db):
    settings, db, session = session_db
    add(db, "Established durable event.")
    calls = []

    def generate(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return "broken JSON"
        db.execute("UPDATE memory_jobs SET lease_token='replacement' WHERE layer='curator'")
        db.commit()
        return VALID

    assert run_curator(settings, db, session, generate) == "stale_source"
    assert calls == [1, 1]
    assert get_curated_memory_state(db, "chat", "s1") == ([], 0)
    assert db.execute("SELECT covered_id FROM memory_layer_state WHERE layer='curator'").fetchone() == (0,)
    assert db.execute("SELECT lease_token,completed_version FROM memory_jobs WHERE layer='curator'").fetchone() == (
        "replacement",
        0,
    )


def test_curator_second_capped_response_remains_unaccepted(session_db):
    settings, db, session = session_db
    add(db, "Established durable event.")
    calls = []

    def generate(*args, **kwargs):
        calls.append(1)
        raise StructuredOutputCapped(VALID)

    assert run_curator(settings, db, session, generate) == "output_capped"
    assert calls == [1, 1]
    assert get_curated_memory_state(db, "chat", "s1") == ([], 0)
    assert db.execute("SELECT covered_id FROM memory_layer_state WHERE layer='curator'").fetchone() == (0,)


def test_curator_provider_failure_is_not_a_format_retry(session_db, caplog):
    settings, db, session = session_db
    add(db, "Established durable event.")
    calls = []

    def generate(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("PRIVATE_PROVIDER_CANARY")

    assert run_curator(settings, db, session, generate) == "work_failed"
    assert calls == [1]
    assert "PRIVATE_PROVIDER_CANARY" not in caplog.text


@pytest.mark.parametrize("code", ["malformed_json", "invalid_shape", "output_capped"])
def test_curator_classification_retains_autonomous_retry_limit(session_db, code):
    _, db, _ = session_db
    add(db)
    db.execute(
        "UPDATE memory_jobs SET attempts=?,last_error=?,next_attempt_at=0 WHERE layer='curator'",
        (AUTO_FAILURE_LIMIT, code),
    )
    db.commit()
    assert claim_jobs(db, layers=("curator",), autonomous=True) == []


def test_curator_parser_normalizes_valid_items_and_accepts_explicit_empty_output():
    assert parse_curator_response('{"memories":[]}') == {"memories": []}
    parsed = parse_curator_response('```json\n{"memories":[{"text":"  Synthetic  fact.  "}]}\n```')["memories"]
    assert parsed[0]["text"] == "Synthetic fact."
    assert parsed[0]["key"].startswith("fact-")
    assert parsed[0]["kind"] == "fact"
    assert parsed[0]["confidence"] == 1.0
