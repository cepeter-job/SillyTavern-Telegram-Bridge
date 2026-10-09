"""Exercise real provider adapters against local fake HTTP responses only."""

import io
import json
from dataclasses import replace

import pytest
from test_request_observation import prompt, runtime
from test_token_usage_transport import setup, stream


def response(text="Visible", reason="stop", inputs=10, cached=4):
    return io.BytesIO(
        json.dumps(
            {
                "choices": [{"message": {"content": text}, "finish_reason": reason}],
                "usage": {
                    "prompt_tokens": inputs,
                    "completion_tokens": 2,
                    "prompt_tokens_details": {"cached_tokens": cached},
                },
            }
        ).encode()
    )


def test_observer_is_wired_at_actual_send_and_never_changes_body(tmp_path, monkeypatch):
    obs = runtime(emit=lambda fields: None)
    port, usage, sent = setup(tmp_path, monkeypatch, [response(), response()])
    messages = [{"role": "system", "content": "PRIVATE_POLICY"}, {"role": "user", "content": "PRIVATE_TEXT"}]
    assert port.generate("PRIVATE_KEY", "p::m", messages) == "Visible"
    assert replace(port, request_observer=obs).generate("PRIVATE_KEY", "p::m", messages) == "Visible"
    assert sent[0] == sent[1]
    report = obs.report()
    assert report["requests_started"] == report["requests_finished"] == 1
    row = report["records"][0]
    assert row["bytes"] == len(json.dumps(sent[1]).encode())
    assert row["input_tokens"] == 10 and row["cached_tokens"] == 4
    assert row["purpose"] == "story" and row["transport"] == "chat_completions"
    assert "PRIVATE" not in json.dumps(report)
    assert len(usage) == 2


def test_each_automatic_continuation_keeps_its_own_usage(tmp_path, monkeypatch):
    obs = runtime(emit=lambda fields: None)
    port, usage, sent = setup(tmp_path, monkeypatch, [response("First", "length", 10), response("Second", "stop", 20)])
    port = replace(port, request_observer=obs)
    assert port.generate("key", "p::m", [{"role": "user", "content": "Continue"}]) == "First Second"
    report = obs.report()
    assert len(sent) == report["requests_finished"] == 2
    by_phase = {r["phase"]: r["input_tokens"] for r in report["records"]}
    assert by_phase == {"initial": 10, "continuation": 20}
    assert report["window_usage"]["known_input_tokens"] == 30
    assert sum(r.input_tokens for r in usage[0].readings) == 30


def test_failed_send_is_recorded_once_and_does_not_trigger_extra_retry(tmp_path, monkeypatch):
    obs = runtime(emit=lambda fields: None)
    port, _usage, sent = setup(tmp_path, monkeypatch, [])
    port = replace(port, request_observer=obs)
    with pytest.raises(IndexError):
        port.generate("key", "p::m", [{"role": "user", "content": "Request"}])
    row = obs.report()["records"][0]
    assert len(sent) == 1
    assert row["input_tokens"] is None and row["status"] == "failed"


def test_stream_cumulative_usage_is_counted_once(tmp_path, monkeypatch):
    obs = runtime(emit=lambda fields: None)
    event = {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 3}}
    port, _, sent = setup(
        tmp_path,
        monkeypatch,
        [
            stream(
                {"choices": [{"delta": {"content": "Visible"}, "finish_reason": "stop"}]},
                event,
                event,
            )
        ],
        streaming=True,
    )
    assert replace(port, request_observer=obs).generate("key", "p::m", [{"role": "user", "content": "Hi"}]) == "Visible"
    report = obs.report()
    assert report["requests_finished"] == 1
    assert report["window_usage"]["known_input_tokens"] == 12
    assert report["records"][0]["usage_readings"] == 1
    assert sent[0]["stream_options"] == {"include_usage": True}


def test_native_context_binding_survives_final_prompt_metadata_removal(tmp_path, monkeypatch):
    obs = runtime(emit=lambda fields: None)
    port, _, _ = setup(tmp_path, monkeypatch, [response(), response()])
    port = replace(port, request_observer=obs).for_usage("PRIVATE_CHAT", "PRIVATE_SESSION", "story")
    messages, session = prompt()
    port = port.with_request_context(messages, session)
    wire = [{k: v for k, v in m.items() if not k.startswith("_")} for m in messages]
    port.generate("key", "p::m", wire)
    port.generate("key", "p::m", wire)
    assert obs.report()["records"][-1]["stable_prefix_characters"] > 0
    assert "PRIVATE" not in json.dumps(obs.report())


def test_fallback_requests_have_separate_route_and_usage_records(tmp_path, monkeypatch):
    import urllib.error
    from functools import partial
    from types import SimpleNamespace

    from settings_test_support import make_test_settings

    from bridge import provider_transport
    from bridge.model_router import ModelRouter
    from bridge.provider_port import ProviderPort

    class Policy:
        def candidates(self, model, purpose):
            return ("p::one", "q::two")

        def begin(self, model):
            return SimpleNamespace(selection=model)

        def fail(self, *args):
            pass

        def succeed(self, *args):
            pass

        def cancel(self, *args):
            pass

    obs = runtime(emit=lambda fields: None)
    router = ModelRouter(
        lambda: {p: {"models": [m], "api_endpoint": f"https://{p}.test/v1"} for p, m in [("p", "one"), ("q", "two")]}
    )
    settings = make_test_settings({"LLM_API_KEY": "PRIVATE_KEY"}, home=tmp_path)
    monkeypatch.setattr(provider_transport, "validate_provider_endpoint", lambda *a, **k: None)
    attempts = []

    def open_request(request, **kwargs):
        attempts.append(request.full_url)
        if len(attempts) == 1:
            raise urllib.error.HTTPError(request.full_url, 503, "PRIVATE", {}, None)
        return response(inputs=20)

    monkeypatch.setattr(provider_transport, "strict_urlopen", open_request)
    port = ProviderPort(
        partial(provider_transport.generate_provider_text, router, app_settings=settings),
        request_observer=obs,
        policy=Policy(),
    ).for_usage("c", "s", "director")
    assert port.generate("key", "p::one", [{"role": "user", "content": "PRIVATE"}]) == "Visible"
    records = sorted(obs.report()["records"], key=lambda r: r["observation_ordinal"])
    assert len(attempts) == len(records) == 2
    assert records[0]["input_tokens"] is None and records[0]["attempt"] == 1
    assert records[1]["input_tokens"] == 20 and records[1]["attempt"] == 2
    assert records[0]["provider_ref"] != records[1]["provider_ref"]
    assert "PRIVATE" not in json.dumps(obs.report())


def test_preflight_rejection_is_not_counted_as_a_send(tmp_path, monkeypatch):
    from bridge import provider_transport

    obs = runtime(emit=lambda fields: None)
    port, _, sent = setup(tmp_path, monkeypatch, [])

    def reject(*args, **kwargs):
        raise ValueError("budget_rejected")

    monkeypatch.setattr(provider_transport, "check_attempt_budget", reject)
    with pytest.raises(ValueError, match="budget_rejected"):
        replace(port, request_observer=obs).generate("key", "p::m", [])
    assert sent == [] and obs.report()["requests_started"] == 0


def test_disabled_nested_port_cannot_inherit_an_outer_observer(tmp_path, monkeypatch):
    from bridge.request_observation_context import logical_observation
    from bridge.token_usage_values import UsageScope

    obs = runtime(emit=lambda fields: None)
    port, _, sent = setup(tmp_path, monkeypatch, [response()])
    with logical_observation(obs, UsageScope("c", "s", "story"), None):
        assert port.generate("key", "p::m", []) == "Visible"
    assert len(sent) == 1 and obs.report()["requests_started"] == 0


def test_final_role_sizes_include_system_and_user_payload_without_guessing_sections(tmp_path, monkeypatch):
    obs = runtime(emit=lambda fields: None)
    port, _, _sent = setup(tmp_path, monkeypatch, [response()])
    wire = [
        {"role": "system", "content": "System and changing summary"},
        {"role": "assistant", "content": "History"},
        {"role": "user", "content": "Current task"},
    ]
    replace(port, request_observer=obs).generate("key", "p::m", wire)
    row = obs.report()["records"][0]
    assert row["instruction_characters"] == len(wire[0]["content"])
    assert row["assistant_characters"] == len(wire[1]["content"])
    assert row["user_characters"] == len(wire[2]["content"])
    assert row["section_attribution"] == "wire_roles_only"
    assert row["full_prompt_token_count_known"] is False


@pytest.mark.parametrize("transport", ["anthropic_messages", "opencode_muse", "openai_codex"])
def test_normalized_transports_observe_actual_wire_body_and_reported_usage(tmp_path, monkeypatch, transport):
    from functools import partial

    from settings_test_support import make_test_settings

    from bridge import codex_transport, provider_transport
    from bridge.model_router import ModelRouter
    from bridge.provider_port import ProviderPort

    obs = runtime(emit=lambda fields: None)
    settings = make_test_settings(home=tmp_path)
    spec = {"models": ["m"], "transport": transport, "api_endpoint": "https://example.test/v1"}
    router = ModelRouter(lambda: {"p": spec})
    sent = []

    def opener(request, **kwargs):
        sent.append(request.data)
        if transport == "anthropic_messages":
            return stream(
                {"type": "message_start", "message": {"usage": {"input_tokens": 10, "cache_read_input_tokens": 4}}},
                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Visible"}},
                {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}},
                {"type": "message_stop"},
            )
        return stream(
            {"type": "response.output_text.delta", "delta": "Visible"},
            {
                "type": "response.completed",
                "response": {
                    "output_text": "Visible",
                    "usage": {"input_tokens": 14, "output_tokens": 2, "input_tokens_details": {"cached_tokens": 4}},
                },
            },
        )

    monkeypatch.setattr(provider_transport, "validate_provider_endpoint", lambda *a, **k: None)
    monkeypatch.setattr(provider_transport, "strict_urlopen", opener)
    monkeypatch.setattr(codex_transport, "validate_codex_endpoint", lambda *a, **k: "https://example.test/v1")
    monkeypatch.setattr(codex_transport, "resolve_access_token", lambda *a, **k: "PRIVATE")
    monkeypatch.setattr(codex_transport, "codex_headers", lambda *a, **k: {})
    monkeypatch.setattr(
        provider_transport,
        "generate_codex_response",
        partial(codex_transport.generate_codex_response, open_request=opener),
    )
    port = ProviderPort(
        partial(provider_transport.generate_provider_text, router, app_settings=settings), request_observer=obs
    ).for_usage("c", "s", "story")
    assert (
        port.generate(
            "key",
            "p::m",
            [{"role": "system", "content": "PRIVATE_POLICY"}, {"role": "user", "content": "PRIVATE_QUESTION"}],
        )
        == "Visible"
    )
    record = obs.report()["records"][0]
    assert len(sent) == 1 and record["bytes"] == len(sent[0])
    assert record["input_tokens"] == 14 and record["cached_tokens"] == 4
    assert record["usage_readings"] == 1 and record["usage_complete"] is True
    assert record["instruction_characters"] > 0
    assert record["transport"] == transport
    assert "PRIVATE" not in json.dumps(obs.report())


def test_codex_auth_retry_does_not_hide_first_unknown_generation_attempt(tmp_path, monkeypatch):
    import urllib.error
    from functools import partial

    from settings_test_support import make_test_settings

    from bridge import codex_transport, provider_transport
    from bridge.model_router import ModelRouter
    from bridge.provider_port import ProviderPort

    obs = runtime(emit=lambda fields: None)
    requests, auth = [], []

    def resolve(*args, **kwargs):
        auth.append(kwargs)
        return "PRIVATE"

    def opener(request, **kwargs):
        requests.append(request.data)
        if len(requests) == 1:
            raise urllib.error.HTTPError(request.full_url, 401, "PRIVATE", {}, None)
        return stream(
            {
                "type": "response.completed",
                "response": {"output_text": "Visible", "usage": {"input_tokens": 10, "output_tokens": 2}},
            }
        )

    monkeypatch.setattr(codex_transport, "validate_codex_endpoint", lambda *a, **k: "https://example.test/v1")
    monkeypatch.setattr(codex_transport, "resolve_access_token", resolve)
    monkeypatch.setattr(codex_transport, "codex_headers", lambda *a, **k: {})
    monkeypatch.setattr(
        provider_transport,
        "generate_codex_response",
        partial(codex_transport.generate_codex_response, open_request=opener),
    )
    router = ModelRouter(lambda: {"p": {"models": ["m"], "transport": "openai_codex"}})
    port = ProviderPort(
        partial(provider_transport.generate_provider_text, router, app_settings=make_test_settings(home=tmp_path)),
        request_observer=obs,
    ).for_usage("c", "s", "story")
    assert port.generate("", "p::m", []) == "Visible"
    records = obs.report()["records"]
    assert len(auth) == len(requests) == len(records) == 2
    assert requests[0] == requests[1]
    assert records[0]["phase"] == "initial" and records[0]["input_tokens"] is None
    assert records[1]["phase"] == "auth_retry" and records[1]["input_tokens"] == 10


def test_stream_cancellation_preserves_unknown_completion(tmp_path, monkeypatch):
    import threading

    cancel = threading.Event()
    obs = runtime(emit=lambda fields: None)
    event = {"choices": [{"delta": {"content": "Partial"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 2}}
    port, _, _ = setup(tmp_path, monkeypatch, [stream(event)], streaming=True)
    text = replace(port, request_observer=obs).generate(
        "key", "p::m", [], cancel_event=cancel, stream_callback=lambda value: cancel.set()
    )
    assert text == "Partial"
    row = obs.report()["records"][0]
    assert row["input_tokens"] == 10 and row["usage_complete"] is False
    assert obs.report()["window_usage"]["complete_input_tokens"] is None
