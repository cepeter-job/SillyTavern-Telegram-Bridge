"""Actual transport attempts account for route, output and normalized payload."""

import io
import json
import urllib.error
from functools import partial
from types import SimpleNamespace

import pytest
from settings_test_support import make_test_settings
from test_story_memory_scope import db as db

from bridge import codex_transport, provider_transport
from bridge.context_compaction import ContextWindowBudgetError, estimate_message_tokens
from bridge.model_router import ModelRouter
from bridge.provider_port import ProviderPort


class Response:
    status = 200

    def __init__(self, content, reason="stop", streaming=False):
        event = {"choices": [{"message": {"content": content}, "delta": {"content": content}, "finish_reason": reason}]}
        self.raw = (f"data: {json.dumps(event)}\n\ndata: [DONE]\n" if streaming else json.dumps(event)).encode()
        self.buffer = io.BytesIO(self.raw)

    def read(self, size=-1):
        return self.buffer.read(size)

    def __iter__(self):
        return iter(self.raw.splitlines(keepends=True))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def setup_route(tmp_path, *, window=8000, transport="openai_compatible", streaming=False, extra=None):
    spec = {
        "models": ["synthetic"],
        "transport": transport,
        "context_window_tokens": window,
        "api_endpoint": "https://example.com/v1",
        "api_key_env": "SYNTHETIC_KEY",
        "streaming": streaming,
    }
    catalog = {"p": spec, **(extra or {})}
    path = tmp_path / "providers.json"
    path.write_text(json.dumps({"providers": catalog}))
    settings = make_test_settings(
        {"SILLYTAVERN_PROVIDER_ALLOWED_HOSTS": "example.com", "SYNTHETIC_KEY": "synthetic-only"},
        home=tmp_path,
        provider_config_file=path,
        context_window_tokens=window,
    )
    return settings, ModelRouter(load_catalog=lambda: catalog)


@pytest.fixture(autouse=True)
def no_real_provider_or_oauth(monkeypatch):
    monkeypatch.setattr(
        provider_transport, "strict_urlopen", lambda *a, **k: pytest.fail("Unexpected provider request")
    )
    monkeypatch.setattr(codex_transport, "resolve_access_token", lambda *a, **k: pytest.fail("Unexpected OAuth read"))


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("output,window", [(3000, 8000), (6000, 16000)])
def test_empty_length_recovery_rechecks_larger_output_before_second_request(
    tmp_path, monkeypatch, streaming, output, window
):
    settings, router = setup_route(tmp_path, window=window, streaming=streaming)
    calls = []

    def request(req, **kwargs):
        calls.append(json.loads(req.data))
        if len(calls) > 1:
            pytest.fail("Recovery output increase exceeded the actual model window")
        return Response("", "length", streaming)

    monkeypatch.setattr(provider_transport, "strict_urlopen", request)
    with pytest.raises(ContextWindowBudgetError) as exc:
        provider_transport.generate_provider_text(
            router,
            "",
            "p::synthetic",
            [{"role": "user", "content": "x" * 14000}],
            settings={"max_tokens": output},
            app_settings=settings,
        )
    assert len(calls) == 1
    assert exc.value.stats["output_reserve_tokens"] == output * 2


@pytest.mark.parametrize("streaming", [False, True])
def test_overflowing_automatic_continuation_preserves_visible_prefix(tmp_path, monkeypatch, streaming):
    settings, router = setup_route(tmp_path, window=4096, streaming=streaming)
    prefix = "Visible prefix " + "v" * 4000
    calls = []

    def request(req, **kwargs):
        calls.append(json.loads(req.data))
        if len(calls) > 1:
            pytest.fail("An oversized appended continuation reached the network")
        return Response(prefix, "length", streaming)

    monkeypatch.setattr(provider_transport, "strict_urlopen", request)
    result = provider_transport.generate_provider_text(
        router,
        "",
        "p::synthetic",
        [{"role": "user", "content": "x" * 5000}],
        settings={"max_tokens": 1800},
        app_settings=settings,
    )
    assert result == prefix
    assert len(calls) == 1


def test_fallback_rechecks_actual_smaller_model_and_ratio(tmp_path, monkeypatch):
    extra = {
        "small": {
            "models": ["synthetic"],
            "transport": "openai_compatible",
            "context_window_tokens": 4096,
            "token_estimate_chars_per_token": 1.5,
            "api_endpoint": "https://example.com/v1",
            "api_key_env": "SYNTHETIC_KEY",
        }
    }
    settings, router = setup_route(tmp_path, window=16000, extra=extra)
    calls = []

    def request(req, **kwargs):
        calls.append(req)
        if len(calls) > 1:
            pytest.fail("Fallback bypassed its own smaller window")
        raise urllib.error.URLError("synthetic unavailable")

    monkeypatch.setattr(provider_transport, "strict_urlopen", request)
    policy = SimpleNamespace(
        candidates=lambda *a: ("p::synthetic", "small::synthetic"),
        begin=lambda selection: SimpleNamespace(selection=selection),
        cancel=lambda *a: None,
        fail=lambda *a: None,
        succeed=lambda *a: None,
    )
    port = ProviderPort(
        generate_backend=partial(provider_transport.generate_provider_text, router, app_settings=settings),
        policy=policy,
    )
    with pytest.raises(ContextWindowBudgetError) as exc:
        port.generate("", "p::synthetic", [{"role": "user", "content": "x" * 6000}], settings={"max_tokens": 1800})
    assert len(calls) == 1
    assert exc.value.stats["window_tokens"] == 4096
    assert exc.value.stats["chars_per_token"] == 1.5


def test_muse_minimum_output_is_reserved_before_request(tmp_path):
    settings, router = setup_route(tmp_path, window=4096, transport="opencode_muse")
    with pytest.raises(ContextWindowBudgetError) as exc:
        provider_transport.generate_provider_text(
            router,
            "",
            "p::synthetic",
            [{"role": "user", "content": "x" * 3000}],
            settings={"max_tokens": 1800},
            app_settings=settings,
        )
    assert exc.value.stats["requested_output_tokens"] == 1800
    assert exc.value.stats["output_reserve_tokens"] == 3000


def test_codex_normalized_default_instructions_are_checked_before_oauth(tmp_path):
    settings, _ = setup_route(tmp_path, window=4096, transport="openai_codex")
    messages = [{"role": "user", "content": "x" * 7000}]
    assert estimate_message_tokens(messages) < 1784
    with pytest.raises(ContextWindowBudgetError) as exc:
        codex_transport.generate_codex_response(
            "synthetic",
            messages,
            {"max_tokens": 1800},
            {},
            "synthetic",
            app_settings=settings,
            open_request=lambda *a, **k: pytest.fail("Unexpected native wire request"),
        )
    assert exc.value.stats["output_reservation_only"] is True


def test_attempt_observer_records_normalized_wire_stats_without_codex_output_cap(tmp_path, monkeypatch):
    settings, _ = setup_route(tmp_path, window=16000, transport="openai_codex")
    monkeypatch.setattr(codex_transport, "resolve_access_token", lambda *a, **k: "synthetic-only")
    monkeypatch.setattr(codex_transport, "validate_codex_endpoint", lambda *a, **k: "https://example.com")
    monkeypatch.setattr(codex_transport, "codex_headers", lambda *a, **k: {})
    bodies = []

    def request(req, **kwargs):
        bodies.append(json.loads(req.data))
        response = Response("")
        response.raw = (
            b'data: {"type":"response.output_text.delta","delta":"ok"}\n\n'
            b'data: {"type":"response.completed","response":{}}\n'
        )
        response.buffer = io.BytesIO(response.raw)
        response.readline = response.buffer.readline
        return response

    attempts = []
    result = codex_transport.generate_codex_response(
        "synthetic",
        [{"role": "user", "content": "CURRENT"}],
        {"max_tokens": 1800},
        {},
        "synthetic",
        app_settings=settings,
        open_request=request,
        context_observer=attempts.append,
    )
    assert result == "ok" and len(bodies) == 1
    assert "max_output_tokens" not in bodies[0] and "max_tokens" not in bodies[0]
    assert attempts[-1]["output_reserve_tokens"] == 1800 and attempts[-1]["output_reservation_only"] is True
    wire = [{"role": "system", "content": bodies[0]["instructions"]}, *bodies[0]["input"]]
    assert attempts[-1]["final_tokens"] == estimate_message_tokens(wire)
    assert "CURRENT" not in json.dumps(attempts)


@pytest.mark.parametrize("transport", ["anthropic_messages", "opencode_muse"])
def test_normalized_adapter_payload_and_attempt_diagnostics_agree(tmp_path, monkeypatch, transport):
    settings, router = setup_route(tmp_path, window=65536, transport=transport)
    bodies = []

    def request(req, **kwargs):
        bodies.append(json.loads(req.data))
        result = Response("")
        result.raw = (
            b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"ok"}}\n'
            b'data: {"type":"message_stop"}\n'
            if transport == "anthropic_messages"
            else b'data: {"type":"response.output_text.delta","delta":"ok"}\n'
            b'data: {"type":"response.completed","response":{}}\n'
        )
        result.buffer = io.BytesIO(result.raw)
        return result

    monkeypatch.setattr(provider_transport, "strict_urlopen", request)
    messages = [
        {"role": "system", "content": "Fixed"},
        {"role": "assistant", "content": "Opening"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "CURRENT"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + "A" * 100000}},
            ],
        },
    ]
    attempts = []
    assert (
        provider_transport.generate_provider_text(
            router,
            "",
            "p::synthetic",
            messages,
            settings={"max_tokens": 1800},
            app_settings=settings,
            context_observer=attempts.append,
        )
        == "ok"
    )
    assert len(bodies) == 1
    body = bodies[0]
    if transport == "anthropic_messages":
        wire = [{"role": "system", "content": body["system"]}, *body["messages"]]
        assert body["messages"][0]["content"][1]["source"]["data"] == "A" * 100000
        assert body["max_tokens"] == attempts[-1]["output_reserve_tokens"] == 1800
    else:
        wire = [*body["input"], {"role": "system", "content": json.dumps(body["tools"], ensure_ascii=False)}]
        assert body["max_output_tokens"] == attempts[-1]["output_reserve_tokens"] == 3000
        assert body["tools"], "Fingerprint tool definitions are part of the normalized prompt budget."
        image = body["input"][-1]["content"][-1]
        assert image["type"] == "input_image"
        assert image["image_url"] == "data:image/jpeg;base64," + "A" * 100000
    assert attempts[-1]["requested_output_tokens"] == 1800
    assert attempts[-1]["final_tokens"] == estimate_message_tokens(wire)
    assert "CURRENT" not in json.dumps(attempts)


def test_successful_fallback_persists_actual_attempt_stats_on_application_thread(db, tmp_path, monkeypatch):

    from bridge.context_diagnostics import context_stats_key, record_context_attempts, save_context_stats
    from bridge.metadata import get_meta

    extra = {
        "fallback": {
            "models": ["synthetic"],
            "transport": "openai_compatible",
            "context_window_tokens": 12000,
            "token_estimate_chars_per_token": 1.5,
            "api_endpoint": "https://example.com/v1",
            "api_key_env": "SYNTHETIC_KEY",
        }
    }
    settings, router = setup_route(tmp_path, window=16000, extra=extra)
    calls = []

    def request(req, **kwargs):
        calls.append(json.loads(req.data))
        if len(calls) == 1:
            raise urllib.error.URLError("synthetic unavailable")
        return Response("ok")

    monkeypatch.setattr(provider_transport, "strict_urlopen", request)
    policy = SimpleNamespace(
        candidates=lambda *a: ("p::synthetic", "fallback::synthetic"),
        begin=lambda selection: SimpleNamespace(selection=selection),
        cancel=lambda *a: None,
        fail=lambda *a: None,
        succeed=lambda *a: None,
    )
    port = ProviderPort(
        generate_backend=partial(provider_transport.generate_provider_text, router, app_settings=settings),
        policy=policy,
    )
    connection = db
    save_context_stats(connection, "c", "s", {"dropped_history": 3, "memory_trimmed": True})
    with record_context_attempts(connection, "c", "s") as observer:
        assert (
            port.with_context_observer(observer).generate(
                "",
                "p::synthetic",
                [{"role": "user", "content": "CURRENT " + "x" * 4000}],
                settings={"max_tokens": 1800},
            )
            == "ok"
        )
    stats = json.loads(get_meta(connection, context_stats_key("c", "s"), ""))
    assert stats["model"] == "fallback::synthetic" and stats["window_tokens"] == 12000
    assert stats["chars_per_token"] == 1.5 and stats["request_stage"] == "chat"
    assert stats["dropped_history"] == 3 and stats["memory_trimmed"] is True
    assert stats["final_tokens"] == estimate_message_tokens(calls[-1]["messages"], chars_per_token=1.5)
    assert "CURRENT" not in json.dumps(stats)
