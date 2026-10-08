"""Truncated JSON helpers must not invent dialogue or send unbounded continuations."""

import io
import json
from functools import partial

import pytest
from settings_test_support import make_test_settings

import bridge.provider_transport as transport
from bridge.memory_artifact_store import parse_classified_response
from bridge.memory_response import generate_memory_response
from bridge.model_router import ModelRoute
from bridge.provider_port import ProviderPort


class FakeResponse(io.BytesIO):
    status = 200

    def __init__(self, data):
        super().__init__(json.dumps(data).encode())


def routed_provider(
    tmp_path, monkeypatch, payloads, *, provider="openrouter", host="openrouter.ai", model_id="test/model"
):
    settings = make_test_settings(
        environ={"TEST_MEMORY_KEY": "synthetic-key", "SILLYTAVERN_PROVIDER_ALLOWED_HOSTS": host},
        home=tmp_path,
    )
    spec = {
        "transport": "openai_compatible",
        "api_endpoint": "https://" + host + "/api/v1",
        "api_key_env": "TEST_MEMORY_KEY",
    }
    router = type("Router", (), {"route": lambda _self, _: ModelRoute(provider, model_id, spec)})()
    requests = []

    def fake_urlopen(request, timeout, *, environ=None):
        requests.append(json.loads(request.data.decode("utf-8")))
        if not payloads:
            pytest.fail("Provider transport sent an unbounded additional request")
        return FakeResponse(payloads.pop(0))

    monkeypatch.setattr(transport, "strict_urlopen", fake_urlopen)
    return ProviderPort(partial(transport.generate_provider_text, router, app_settings=settings)), requests


def test_memory_json_length_response_reextracts_from_canonical_input_without_dialogue_continuation(
    tmp_path, monkeypatch
):
    payloads = [
        {"choices": [{"message": {"content": '{"blocks":'}, "finish_reason": "length"}]},
        {
            "choices": [
                {
                    "message": {
                        "content": '{"blocks":[{"text":"Established fact","visibility":"shared","known_by":[]}]}'
                    },
                    "finish_reason": "stop",
                }
            ]
        },
    ]
    port, sent = routed_provider(tmp_path, monkeypatch, payloads)
    source = [
        {"role": "system", "content": "Extract durable classified facts"},
        {"role": "user", "content": "Canonical source fact"},
    ]
    result = generate_memory_response(
        port.generate,
        "",
        "openrouter::test/model",
        source,
        parser=parse_classified_response,
        session_id="synthetic",
        settings={"max_tokens": 1200},
    )
    assert result["blocks"][0]["text"] == "Established fact"
    assert len(sent) == 2
    assert sent[0]["messages"] == source
    assert sent[1]["messages"][-1] == source[-1]
    assert all(not any("Continue from the exact ending" in msg["content"] for msg in call["messages"]) for call in sent)
    assert sent[0]["max_tokens"] == sent[1]["max_tokens"] == 1200


def test_memory_json_empty_length_response_fails_once_without_transport_retry(tmp_path, monkeypatch):
    port, sent = routed_provider(
        tmp_path,
        monkeypatch,
        [{"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}],
    )
    with pytest.raises(RuntimeError):
        generate_memory_response(
            port.generate,
            "",
            "openrouter::test/model",
            [{"role": "user", "content": "Canon"}],
            parser=parse_classified_response,
            session_id="synthetic",
            settings={"max_tokens": 1200},
        )
    assert len(sent) == 1


def test_adaptive_summary_output_budget_never_reduces_or_exceeds_bounded_allocation():
    from bridge.memory import summary_output_budget

    small = {"blocks": [{"text": "small", "visibility": "shared", "known_by": []}]}
    large = {"blocks": [{"text": "X" * 13500, "visibility": "shared", "known_by": []}]}
    assert summary_output_budget(small) == 1200
    assert summary_output_budget(large) == 4096
    assert 1200 <= summary_output_budget({"blocks": [{"text": "X" * 4200}]}) <= 4096


def test_nanogpt_structured_helper_adds_json_object_only_for_compatible_endpoint(tmp_path, monkeypatch):
    payloads = [
        {"choices": [{"message": {"content": '{"blocks":[]}'}, "finish_reason": "stop"}]},
        {"choices": [{"message": {"content": "Ordinary story response"}, "finish_reason": "stop"}]},
    ]
    port, sent = routed_provider(
        tmp_path, monkeypatch, payloads, provider="nano-gpt", host="nano-gpt.com", model_id="z-ai/glm-5.2"
    )
    assert generate_memory_response(
        port.generate,
        "",
        "nano-gpt::test/model",
        [{"role": "system", "content": "Return JSON"}, {"role": "user", "content": "Synthetic"}],
        parser=parse_classified_response,
        session_id="synthetic",
        settings={"max_tokens": 1200},
    ) == {"blocks": []}
    assert (
        port.generate(
            "",
            "nano-gpt::test/model",
            [{"role": "user", "content": "Ordinary story response"}],
            session_id="story",
            settings={"max_tokens": 1200},
            force_non_stream=True,
        )
        == "Ordinary story response"
    )
    assert sent[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in sent[1]


def test_json_mode_is_never_implicitly_used_for_other_openai_providers(tmp_path, monkeypatch):
    port, sent = routed_provider(
        tmp_path, monkeypatch, [{"choices": [{"message": {"content": '{"blocks":[]}'}, "finish_reason": "stop"}]}]
    )
    assert generate_memory_response(
        port.generate,
        "",
        "openrouter::test/model",
        [{"role": "system", "content": "Return JSON"}],
        parser=parse_classified_response,
        session_id="synthetic",
        settings={"max_tokens": 1200},
    ) == {"blocks": []}
    assert "response_format" not in sent[0]


def test_other_nanogpt_models_do_not_inherit_untested_json_mode(tmp_path, monkeypatch):
    port, sent = routed_provider(
        tmp_path,
        monkeypatch,
        [{"choices": [{"message": {"content": '{"blocks":[]}'}, "finish_reason": "stop"}]}],
        provider="nano-gpt",
        host="nano-gpt.com",
        model_id="other/untested-model",
    )
    assert generate_memory_response(
        port.generate,
        "",
        "nano-gpt::other/untested-model",
        [{"role": "system", "content": "Return JSON"}],
        parser=parse_classified_response,
        session_id="synthetic",
        settings={"max_tokens": 1200},
    ) == {"blocks": []}
    assert "response_format" not in sent[0]
