"""Choice retries preserve source data, request limits, and diagnostic privacy."""

import json
from dataclasses import replace

import pytest
from test_light_novel_generation import started, story_row
from test_light_novel_storage import novel_db as novel_db

from bridge.diagnostic_logging import DiagnosticFormatter
from bridge.light_novel_service import attach_turn, ensure_choices, prepare_turn
from bridge.provider_completion import StructuredOutputCapped
from bridge.provider_port import ProviderPort


def pending_choice(db, session, count=4):
    started(db)
    record = prepare_turn(db, "chat", session, "recovery-turn", "owner", rng=lambda _: count)
    attach_turn(db, record, story_row(db, "Synthetic story sentinel"), "Synthetic story sentinel")
    return record


def failure_events(caplog):
    return [
        record.diagnostic_fields
        for record in caplog.records
        if getattr(record, "diagnostic_fields", {}).get("event") == "light_novel.choice_failed"
    ]


@pytest.mark.parametrize("count", [3, 4])
def test_invalid_choices_reextract_once_from_original_context(novel_db, caplog, count):
    db, session, settings = novel_db
    record = pending_choice(db, session, count)
    expected = [f"Action {index}" for index in range(count)]
    calls = []

    def generate(_key, model, messages, **kwargs):
        assert not db.in_transaction
        calls.append((model, messages, kwargs))
        return "Synthetic output sentinel" if len(calls) == 1 else json.dumps({"choices": expected})

    result = ensure_choices(db, record.nonce, session, {}, provider_port=ProviderPort(generate), app_settings=settings)
    assert result.generation_status == "ready"
    assert result.choices == tuple(expected)
    assert result.requested_count == count
    assert len(calls) == 2
    assert calls[0][0] == calls[1][0]
    assert calls[0][1][1:] == calls[1][1][1:]
    repair = calls[1][1][0]["content"]
    assert "Synthetic output sentinel" not in repair
    assert "invalid_json" in repair and f"exactly {count}" in repair
    for _, _, kwargs in calls:
        assert kwargs["settings"]["max_tokens"] == 1200
        assert kwargs["settings"]["json_once"] is True
        assert kwargs["force_non_stream"] is True
        assert kwargs["request_timeout"] == 60
    assert db.execute("SELECT content FROM messages").fetchall() == [("Synthetic story sentinel",)]
    assert failure_events(caplog)[0]["retryable"] is True


def test_repeated_invalid_choices_fail_closed_with_private_json_diagnostics(novel_db, caplog):
    db, session, settings = novel_db
    record = pending_choice(db, session)
    calls = []

    def generate(*args, **kwargs):
        calls.append(kwargs)
        return "Synthetic output sentinel"

    result = ensure_choices(db, record.nonce, session, {}, provider_port=ProviderPort(generate), app_settings=settings)
    assert result.generation_status == "failed"
    assert not result.choices
    assert len(calls) == 2
    failures = failure_events(caplog)
    assert len(failures) == 2
    assert [event["retryable"] for event in failures] == [True, False]
    assert [event["attempt"] for event in failures] == [1, 2]
    for event in failures:
        assert event["phase"] == "parse"
        assert event["reason"] == "invalid_json"
        assert event["count"] == 4
        assert event["kind"] == "b"
        assert event["root_type"] == "prose"
        assert event["model_ref"] and event["attempt_ref"]
    rendered = "\n".join(DiagnosticFormatter().format(item) for item in caplog.records)
    rows = [json.loads(line) for line in rendered.splitlines()]
    assert [row["reason"] for row in rows if row["event"] == "light_novel.choice_failed"] == [
        "invalid_json",
        "invalid_json",
    ]
    assert "Synthetic" not in rendered
    assert record.nonce not in rendered
    assert '"chat_id"' not in rendered and '"session_id"' not in rendered


def test_transient_and_validation_failures_share_two_attempts(novel_db, caplog):
    db, session, settings = novel_db
    record = pending_choice(db, session)
    calls = []

    def generate(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise TimeoutError("Synthetic exception sentinel")
        return "Synthetic output sentinel"

    result = ensure_choices(db, record.nonce, session, {}, provider_port=ProviderPort(generate), app_settings=settings)
    assert result.generation_status == "failed"
    assert len(calls) == 2
    failures = failure_events(caplog)
    assert [event["reason"] for event in failures] == ["timeout", "invalid_json"]
    assert [event["retryable"] for event in failures] == [True, False]
    assert "Synthetic" not in repr(failures)


def test_capped_choices_cannot_be_published_even_when_json_is_valid(novel_db, caplog):
    db, session, settings = novel_db
    record = pending_choice(db, session)
    calls = []

    def generate(*args, **kwargs):
        calls.append(kwargs)
        raise StructuredOutputCapped('{"choices":["A","B","C","D"]}')

    result = ensure_choices(db, record.nonce, session, {}, provider_port=ProviderPort(generate), app_settings=settings)
    assert result.generation_status == "failed"
    assert not result.choices
    assert len(calls) == 2
    assert [event["reason"] for event in failure_events(caplog)] == ["output_capped", "output_capped"]
    assert all(call["settings"]["json_once"] is True for call in calls)


@pytest.mark.parametrize("count", [3, 4])
def test_choice_schema_example_matches_the_requested_count(novel_db, count):
    import re

    db, session, settings = novel_db
    record = pending_choice(db, session, count)
    prompts = []

    def generate(_key, _model, messages, **kwargs):
        prompts.append(messages[0]["content"])
        return json.dumps({"choices": [f"Action {index}" for index in range(count)]})

    result = ensure_choices(db, record.nonce, session, {}, provider_port=ProviderPort(generate), app_settings=settings)
    assert result.generation_status == "ready"
    example = json.loads(re.search(r"Return only JSON: (.+?)\. Each action", prompts[0]).group(1))
    assert len(example["choices"]) == count
    assert len(set(example["choices"])) == count


def test_reset_after_invalid_output_stops_reextraction(novel_db):
    from bridge.conversation_lifecycle import reset_conversation

    db, session, settings = novel_db
    record = pending_choice(db, session)
    calls = []

    def generate(*args, **kwargs):
        calls.append(kwargs)
        reset_conversation(db, "chat", "story")
        return "invalid output"

    result = ensure_choices(db, record.nonce, session, {}, provider_port=ProviderPort(generate), app_settings=settings)
    assert len(calls) == 1
    assert result.state == "invalidated"
    assert not result.choices


def test_choice_generation_retries_parse_failure_once_and_logs_safely(novel_db, caplog):
    from bridge.light_novel_service import attach_turn, ensure_choices, prepare_turn

    db, session, settings = novel_db
    settings = replace(settings, api_key="SECRET_API_KEY")
    started(db)
    record = prepare_turn(db, "chat", session, "turn-parse", "owner", rng=lambda _: 3)
    attach_turn(db, record, story_row(db, "SECRET_STORY_TEXT"), "SECRET_STORY_TEXT")
    calls = []
    raw = "SECRET_RAW_OUTPUT not-json"

    def generate(*args, **kwargs):
        calls.append(kwargs["request_timeout"])
        return raw

    with caplog.at_level("WARNING"):
        result = ensure_choices(
            db, record.nonce, session, {}, provider_port=ProviderPort(generate), app_settings=settings
        )

    assert result.generation_status == "failed"
    assert calls == [60, 60]
    failure = failure_events(caplog)[-1]
    assert failure["phase"] == "parse"
    assert failure["reason"] == "invalid_json"
    assert failure["count"] == 3
    assert failure["model_ref"]
    assert failure["retryable"] is False
    assert "SECRET_STORY_TEXT" not in caplog.text
    assert "SECRET_RAW_OUTPUT" not in caplog.text
    assert settings.api_key not in caplog.text
