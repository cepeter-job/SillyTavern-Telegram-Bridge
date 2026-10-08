"""Pure format-recovery tests: no network, live database, or raw diagnostic output."""

import json

import pytest

from bridge.memory_artifact_store import parse_classified_response
from bridge.memory_response import (
    MemorySourceChanged,
    generate_memory_response,
    memory_failure_code,
    memory_scope_reference,
)


def test_repair_preserves_inputs_and_does_not_replay_invalid_output():
    messages = [{"role": "system", "content": "Extract canonical facts"}, {"role": "user", "content": "source"}]
    settings = {"stop_sequences": "story stop", "max_tokens": 100}
    calls = []

    def generate(*args, **kwargs):
        calls.append((args, kwargs))
        return "untrusted broken output" if len(calls) == 1 else "{}"

    assert (
        generate_memory_response(
            generate, "", "model", messages, parser=parse_classified_response, session_id="synthetic", settings=settings
        )
        == {}
    )
    assert len(calls) == 2
    assert calls[1][0][2][1:] == messages
    assert "untrusted broken output" not in json.dumps(calls[1][0][2])
    assert settings["stop_sequences"] == "story stop"
    assert len(messages) == 2
    assert all(call[1]["force_non_stream"] for call in calls)
    assert all(call[1]["settings"]["json_once"] is True for call in calls)


def test_source_invalidated_before_repair_does_not_call_provider_again():
    calls = []

    def generate(*args, **kwargs):
        calls.append(1)
        return "broken"

    with pytest.raises(MemorySourceChanged):
        generate_memory_response(
            generate,
            "",
            "model",
            [],
            parser=parse_classified_response,
            session_id="synthetic",
            settings={},
            source_valid=lambda: False,
        )
    assert calls == [1]


def test_provider_failure_is_not_a_format_retry():
    calls = []

    def generate(*args, **kwargs):
        calls.append(1)
        raise ValueError("PRIVATE_PROVIDER_CANARY")

    with pytest.raises(ValueError, match="PRIVATE_PROVIDER_CANARY"):
        generate_memory_response(
            generate, "", "model", [], parser=parse_classified_response, session_id="synthetic", settings={}
        )
    assert calls == [1]


@pytest.mark.parametrize(
    "message,code",
    [
        ("NPC extractor returned malformed NPC output", "invalid_npc_output"),
        ("NPC extractor returned malformed output", "invalid_npc_output"),
        ("episodic memory response must be a JSON array", "invalid_shape"),
        ("Shared memory cannot carry a conflicting restricted audience", "invalid_audience"),
        ("PRIVATE_PROVIDER_CANARY", "work_failed"),
    ],
)
def test_only_parser_owned_literals_are_classified(message, code):
    assert memory_failure_code(ValueError(message)) == code


def test_scope_correlation_is_stable_and_session_specific():
    first = memory_scope_reference("private-chat", "private-session", 1.0)
    assert first == memory_scope_reference("private-chat", "private-session", 1.0)
    assert first != memory_scope_reference("private-chat", "private-session", 2.0)
    assert len(first) == 16 and "private" not in first
