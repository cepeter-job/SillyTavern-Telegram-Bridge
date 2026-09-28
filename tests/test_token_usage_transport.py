"""Meter real adapter paths with isolated response fixtures, never live providers."""

import io
import json
import threading
from functools import partial

import pytest
from settings_test_support import make_test_settings

from bridge import codex_transport, provider_transport
from bridge.model_router import ModelRouter
from bridge.provider_port import ProviderPort


def stream(*events):
    return io.BytesIO(
        b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events) + b"data: [DONE]\n\n"
    )


def setup(tmp_path, monkeypatch, responses, *, streaming=False):
    settings = make_test_settings(home=tmp_path)
    spec = {"models": ["m"], "api_endpoint": "https://example.test/v1", "streaming": streaming}
    router = ModelRouter(lambda: {"p": spec})
    monkeypatch.setattr(provider_transport, "validate_provider_endpoint", lambda *a, **k: None)
    requests = []

    def open_request(request, **kwargs):
        requests.append(json.loads(request.data))
        return responses.pop(0)

    monkeypatch.setattr(provider_transport, "strict_urlopen", open_request)
    records = []
    backend = partial(provider_transport.generate_provider_text, router, app_settings=settings)
    port = ProviderPort(backend, usage_recorder=records.append).for_usage("123", "session", "story")
    return port, records, requests


def test_json_continuation_accounts_for_each_actual_response(tmp_path, monkeypatch):
    responses = [
        io.BytesIO(
            json.dumps(
                {
                    "choices": [{"message": {"content": text}, "finish_reason": reason}],
                    "usage": {"prompt_tokens": inputs, "completion_tokens": 2},
                }
            ).encode()
        )
        for text, reason, inputs in [("First", "length", 10), ("Second", "stop", 20)]
    ]
    port, records, _ = setup(tmp_path, monkeypatch, responses)
    assert port.generate("fixture-key", "p::m", []) == "First Second"
    assert len(records) == 1 and len(records[0].readings) == 2
    assert sum(r.total_tokens for r in records[0].readings) == 34


def test_openai_stream_final_usage_is_counted_once_and_requested(tmp_path, monkeypatch):
    response = stream(
        {"choices": [{"delta": {"content": "Visible"}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 4}},
        {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 4}},
    )
    port, records, requests = setup(tmp_path, monkeypatch, [response], streaming=True)
    assert port.generate("fixture-key", "p::m", []) == "Visible"
    assert requests[0]["stream_options"] == {"include_usage": True}
    assert len(records[0].readings) == 1
    assert records[0].readings[0].total_tokens == 16


def test_anthropic_adapter_captures_message_start_and_final_delta(tmp_path, monkeypatch):
    response = stream(
        {
            "type": "message_start",
            "message": {"usage": {"input_tokens": 4, "output_tokens": 1, "cache_read_input_tokens": 6}},
        },
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello"}},
        {"type": "message_delta", "usage": {"output_tokens": 3}},
    )
    monkeypatch.setattr(provider_transport, "strict_urlopen", lambda *a, **k: response)
    monkeypatch.setattr(provider_transport, "validate_provider_endpoint", lambda *a, **k: None)
    readings = []
    assert (
        provider_transport.anthropic_generate(
            "key",
            "m",
            [],
            {"max_tokens": 100, "temperature": 0.5},
            {"api_endpoint": "https://example.test"},
            "s",
            app_settings=make_test_settings(home=tmp_path),
            usage_callback=readings.append,
        )
        == "Hello"
    )
    assert readings[0].total_tokens == 13
    assert readings[0].cached_tokens == 6


@pytest.mark.parametrize("parser", ["codex", "muse"])
def test_responses_parsers_keep_completed_usage(parser):
    response = stream(
        {"type": "response.output_text.delta", "delta": "Hello"},
        {
            "type": "response.completed",
            "response": {
                "output_text": "Hello",
                "usage": {
                    "input_tokens": 9,
                    "output_tokens": 4,
                    "input_tokens_details": {"cached_tokens": 2},
                    "output_tokens_details": {"reasoning_tokens": 1},
                },
            },
        },
    )
    readings = []
    if parser == "codex":
        text = codex_transport._stream_response_text(response, usage_callback=readings.append)
    else:
        text = provider_transport._opencode_responses_text(response.read().decode(), usage_callback=readings.append)
    assert text == "Hello"
    assert len(readings) == 1 and readings[0].total_tokens == 13
    assert readings[0].reasoning_tokens == 1


def test_missing_usage_does_not_fabricate_token_estimate(tmp_path, monkeypatch):
    response = io.BytesIO(b'{"choices":[{"message":{"content":"A very long reply"}}]}')
    port, records, _ = setup(tmp_path, monkeypatch, [response])
    assert port.generate("fixture-key", "p::m", []) == "A very long reply"
    assert records[0].readings[0].total_tokens is None
    assert not records[0].readings[0].complete


def test_cancelled_stream_with_partial_usage_is_not_fully_reported(tmp_path, monkeypatch):
    cancel = threading.Event()
    response = stream(
        {
            "choices": [{"delta": {"content": "Partial"}, "finish_reason": None}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        }
    )
    port, records, _ = setup(tmp_path, monkeypatch, [response], streaming=True)
    assert port.generate("key", "p::m", [], cancel_event=cancel, stream_callback=lambda text: cancel.set()) == "Partial"
    assert records[0].status == "cancelled"
    assert records[0].readings[0].total_tokens == 12
    assert not records[0].readings[0].complete


def test_provider_can_disable_stream_usage_extension_without_fake_counts(tmp_path, monkeypatch):

    response = stream({"choices": [{"delta": {"content": "Hello"}, "finish_reason": "stop"}]})
    requests = []
    settings = make_test_settings(home=tmp_path)
    router = ModelRouter(
        lambda: {
            "p": {"models": ["m"], "api_endpoint": "https://example.test/v1", "streaming": True, "stream_usage": False}
        }
    )
    monkeypatch.setattr(provider_transport, "validate_provider_endpoint", lambda *a, **k: None)

    def open_request(request, **kwargs):
        requests.append(json.loads(request.data))
        return response

    monkeypatch.setattr(provider_transport, "strict_urlopen", open_request)
    records = []
    assert (
        provider_transport.generate_provider_text(
            router, "key", "p::m", [], app_settings=settings, usage_callback=records.append
        )
        == "Hello"
    )
    assert "stream_options" not in requests[0]
    assert records[0].total_tokens is None


def test_failed_continuation_marks_coverage_incomplete(tmp_path, monkeypatch):
    first = io.BytesIO(
        b'{"choices":[{"message":{"content":"First"},"finish_reason":"length"}],"usage":{"prompt_tokens":10,"completion_tokens":2}}'
    )
    port, records, _ = setup(tmp_path, monkeypatch, [first])
    assert port.generate("key", "p::m", []) == "First"
    assert sum(u.total_tokens or 0 for u in records[0].readings) == 12
    assert not all(u.complete for u in records[0].readings)


def test_openai_stream_without_terminal_event_keeps_usage_partial(tmp_path, monkeypatch):
    response = stream(
        {"choices": [{"delta": {"content": "Partial"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 2}}
    )
    port, records, _ = setup(tmp_path, monkeypatch, [response], streaming=True)
    assert port.generate("key", "p::m", []) == "Partial"
    assert records[0].readings[0].total_tokens == 12
    assert not records[0].readings[0].complete


def test_responses_stream_without_completion_keeps_usage_partial():
    response = stream(
        {"type": "response.output_text.delta", "delta": "Partial", "usage": {"input_tokens": 10, "output_tokens": 2}}
    )
    readings = []
    assert (
        provider_transport._opencode_responses_text(response.read().decode(), usage_callback=readings.append)
        == "Partial"
    )
    assert readings[0].total_tokens == 12
    assert not readings[0].complete
