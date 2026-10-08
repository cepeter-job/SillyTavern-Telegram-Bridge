"""Provider responses must be bounded before decoding or accumulating text."""

import io
import json

import pytest
from settings_test_support import make_test_settings

from bridge import provider_transport
from bridge.limits import PROVIDER_TEXT_RESPONSE_MAX_BYTES
from bridge.model_router import ModelRouter


class Response(io.BytesIO):
    def __init__(self, payload):
        super().__init__(payload)
        self.bytes_read = 0

    def read(self, size=-1):
        value = super().read(size)
        self.bytes_read += len(value)
        return value

    def readline(self, size=-1):
        value = super().readline(size)
        self.bytes_read += len(value)
        return value


def generate(tmp_path, monkeypatch, transport, payload, **kwargs):
    response = Response(payload)
    monkeypatch.setattr(provider_transport, "strict_urlopen", lambda *a, **k: response)
    monkeypatch.setattr(provider_transport, "validate_provider_endpoint", lambda *a, **k: None)
    spec = {"models": ["m"], "api_endpoint": "https://example.test/v1"}
    if transport == "openai_stream":
        spec["streaming"] = True
    elif transport != "openai_json":
        spec["transport"] = transport
    router = ModelRouter(lambda: {"fixture": spec})
    return response, lambda: provider_transport.generate_provider_text(
        router, "fixture-key", "fixture::m", [], app_settings=make_test_settings(home=tmp_path), **kwargs
    )


def event(transport, text):
    if transport == "openai_stream":
        data = {"choices": [{"delta": {"content": text}, "finish_reason": "stop"}]}
    else:
        data = {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}
    return b"data: " + json.dumps(data).encode() + b"\n\n"


@pytest.mark.parametrize("transport", ["openai_stream", "anthropic_messages"])
def test_stream_rejects_oversized_line_before_decoding(tmp_path, monkeypatch, transport):
    response, request = generate(tmp_path, monkeypatch, transport, event(transport, "x" * (300 * 1024)))
    with pytest.raises(ValueError, match="response exceeded the safety limit"):
        request()
    assert response.closed
    assert response.bytes_read <= 256 * 1024 + 1


@pytest.mark.parametrize("transport", ["openai_stream", "anthropic_messages"])
def test_stream_total_budget_includes_non_data_lines(tmp_path, monkeypatch, transport):
    payload = b": heartbeat\n" * (PROVIDER_TEXT_RESPONSE_MAX_BYTES // 12 + 2) + event(transport, "ok")
    readings = []
    response, request = generate(tmp_path, monkeypatch, transport, payload, usage_callback=readings.append)
    with pytest.raises(ValueError, match="response exceeded the safety limit"):
        request()
    assert response.closed
    assert response.bytes_read <= PROVIDER_TEXT_RESPONSE_MAX_BYTES + 1
    assert len(readings) == 1 and not readings[0].complete


@pytest.mark.parametrize("transport", ["openai_json", "opencode_muse"])
def test_buffered_response_rejects_size_before_decoding(tmp_path, monkeypatch, transport):
    text = "x" * (PROVIDER_TEXT_RESPONSE_MAX_BYTES + 100)
    data = {"output_text": text} if transport == "opencode_muse" else {"choices": [{"message": {"content": text}}]}
    response, request = generate(tmp_path, monkeypatch, transport, json.dumps(data).encode())
    with pytest.raises(ValueError, match="response exceeded the safety limit"):
        request()
    assert response.closed
    assert response.bytes_read == PROVIDER_TEXT_RESPONSE_MAX_BYTES + 1


@pytest.mark.parametrize("transport", ["openai_stream", "anthropic_messages", "openai_json", "opencode_muse"])
def test_bounded_response_preserves_visible_text(tmp_path, monkeypatch, transport):
    if transport in {"openai_stream", "anthropic_messages"}:
        payload = event(transport, "ok")
    elif transport == "openai_json":
        payload = b'{"choices":[{"message":{"content":"ok"}}]}'
    else:
        payload = b'{"output_text":"ok"}'
    response, request = generate(tmp_path, monkeypatch, transport, payload)
    assert request() == "ok"
    assert response.closed


@pytest.mark.parametrize("transport", ["openai_stream", "anthropic_messages"])
def test_terminal_event_finishes_without_waiting_for_connection_close(tmp_path, monkeypatch, transport):
    terminal = b"data: [DONE]\n" if transport == "openai_stream" else b'data: {"type":"message_stop"}\n'
    payload = event(transport, "ok") + terminal + b": after-terminal\n"
    response, request = generate(tmp_path, monkeypatch, transport, payload)
    read_line = response.readline

    def until_terminal(size=-1):
        if response.tell() >= len(payload) - len(b": after-terminal\n"):
            raise RuntimeError("provider kept the connection open after completion")
        return read_line(size)

    response.readline = until_terminal
    assert request() == "ok"
    assert response.closed
