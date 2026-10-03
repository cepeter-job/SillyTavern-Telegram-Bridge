"""Provider controls remain scoped, non-authoritative and free of repeated probes."""

from __future__ import annotations

import importlib
import sqlite3
from dataclasses import replace

import pytest
from test_provider_resilience import open_circuit, setup

from bridge import provider_callbacks as callbacks
from bridge import provider_panels as panels
from bridge.provider_errors import ProviderRequestError
from bridge.request_types import RequestContext
from bridge.settings import load_app_settings


@pytest.fixture
def context(tmp_path):
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)")
    db.execute("CREATE TABLE callback_tokens(token TEXT PRIMARY KEY,kind TEXT,value TEXT,chat_id TEXT,expires_at REAL)")
    yield RequestContext(db, "session-a", "actor-a", app_settings=load_app_settings({}, home=tmp_path))
    db.close()


def handle(context, data, *, policy=None, probes=None, answer=lambda *a: None):
    return callbacks.handle_provider_model_callback(
        context.db,
        "token",
        {"id": "cb"},
        answer,
        data,
        "chat",
        {"message_id": 1},
        {"model_id": "alpha::one"},
        context.session_id,
        None,
        request_context=context,
        provider_policy=policy,
        provider_probes=probes,
    )


def tokens():
    assert importlib.util.find_spec("bridge.provider_panel_tokens") is not None, (
        "bound provider-action tokens are missing"
    )
    return importlib.import_module("bridge.provider_panel_tokens")


def test_force_refresh_acknowledges_before_network_and_redraws_without_discovery(context, monkeypatch):
    calls = []
    monkeypatch.setattr(callbacks, "refresh_model_catalog", lambda **kw: calls.append(("refresh", kw)) or ({}, 0, 1))
    monkeypatch.setattr(callbacks, "send_text", lambda _token, _chat, text: calls.append(("text", text)))
    monkeypatch.setattr(callbacks, "send_model_menu", lambda *a, **kw: calls.append(("menu", kw)))
    handle(context, "provider:refresh", answer=lambda *a: calls.append(("ack", {})))
    assert [name for name, _ in calls] == ["ack", "refresh", "text", "menu"]
    assert calls[2][1] == "Provider catalog refreshed: 0 providers updated; 1 failed."
    assert calls[-1][1]["refresh_catalog"] is False


def test_action_tokens_bind_operation_actor_session_and_chat(context):
    module = tokens()
    token = module.provider_action_token("refresh", "alpha", "chat", request_context=context)
    assert module.resolve_provider_action(token, "refresh", "chat", request_context=context) == "alpha"
    assert module.resolve_provider_action(token, "reset", "chat", request_context=context) is None
    assert module.resolve_provider_action(token, "refresh", "another", request_context=context) is None
    for wrong in (replace(context, actor_id="actor-b"), replace(context, session_id="session-b")):
        assert module.resolve_provider_action(token, "refresh", "chat", request_context=wrong) is None


def test_targeted_refresh_does_not_refresh_other_providers(context, monkeypatch):
    module = tokens()
    token = module.provider_action_token("refresh", "alpha", "chat", request_context=context)
    calls = []
    sent = []
    monkeypatch.setattr(callbacks, "refresh_model_catalog", lambda **kw: calls.append(kw) or ({}, 1, 0))
    monkeypatch.setattr(callbacks, "send_text", lambda _token, _chat, text: sent.append(text))
    monkeypatch.setattr(callbacks, "send_model_menu", lambda *a, **kw: None)
    assert handle(context, f"provider:maint:refresh:{token}")
    assert calls[0]["provider_id"] == "alpha"
    assert calls[0]["force"] is True
    assert sent == ["Provider catalog refreshed: 1 providers updated; 0 failed."]


def test_replayed_action_from_other_actor_does_not_mutate_or_probe(context, monkeypatch):
    module = tokens()
    token = module.provider_action_token("refresh", "alpha", "chat", request_context=context)
    monkeypatch.setattr(callbacks, "refresh_model_catalog", lambda **kw: pytest.fail("unauthorized refresh"))
    monkeypatch.setattr(callbacks, "send_model_menu", lambda *a, **kw: pytest.fail("unauthorized view"))
    answers = []
    assert handle(
        replace(context, actor_id="actor-b"), f"provider:maint:refresh:{token}", answer=lambda *a: answers.append(a[-1])
    )
    assert "expired" in answers[0].lower()


def test_reset_runtime_clears_local_blocks_and_ignores_older_inflight_results():
    _, health, policy = setup()
    old = health.begin("alpha", "two")
    open_circuit(health)
    assert hasattr(policy, "reset"), "explicit runtime reset is missing"
    policy.reset("alpha")
    assert policy.snapshot("alpha").state == "unknown"
    health.fail(old, ProviderRequestError("alpha::two", "authentication", 401))
    assert policy.snapshot("alpha").state == "unknown"
    health.succeed(health.begin("alpha", "one"))
    assert policy.snapshot("alpha").state == "healthy"


def test_health_paging_reuses_probe_report_but_refreshes_runtime_view(context, monkeypatch):
    tokens()
    _, health, policy = setup()
    probes, sent = [], []
    checks = [(f"p{i}", f"Provider {i}", "catalog reachable (HTTP 200)") for i in range(13)]

    def check(**kw):
        probes.append(kw)
        return checks

    monkeypatch.setattr(panels, "provider_health_checks", check)
    monkeypatch.setattr(
        panels, "send_panel_message", lambda token, chat, text, markup, *a, **kw: sent.append((text, markup))
    )
    panels.send_provider_health_menu("token", "chat", 1, request_context=context, provider_policy=policy)
    text, markup = sent[-1]
    assert "Runtime: Unknown" in text
    assert len(text.encode("utf-16-le")) // 2 <= 4096
    next_button = next(b for row in markup["inline_keyboard"] for b in row if b["text"].startswith("Next"))
    handle(context, next_button["callback_data"], policy=policy)
    assert len(probes) == 1
    assert "Provider 4" in sent[-1][0]
    assert "Provider 0\n" not in sent[-1][0]
    assert health.snapshot("p0").state == "unknown"


def test_manual_probe_never_closes_runtime_circuit(context, monkeypatch):
    _, health, policy = setup()
    open_circuit(health)
    sent = []
    monkeypatch.setattr(
        panels, "provider_health_checks", lambda **kw: [("alpha", "Alpha", "catalog reachable (HTTP 200)")]
    )
    monkeypatch.setattr(panels, "send_panel_message", lambda token, chat, text, *a, **kw: sent.append(text))
    panels.send_provider_health_menu("token", "chat", 1, request_context=context, provider_policy=policy)
    assert "Runtime: Cooldown" in sent[0]
    assert "catalog reachable" in sent[0]
    assert health.snapshot("alpha").state == "cooldown"


def test_provider_model_view_contains_bound_test_refresh_and_reset(context, monkeypatch):
    _, _, policy = setup()
    sent = []
    monkeypatch.setattr(panels, "get_model_groups", lambda **kw: {"alpha": ("Alpha", [("one", "alpha::one")], True)})
    monkeypatch.setattr(panels, "send_panel_message", lambda token, chat, text, markup, *a, **kw: sent.append(markup))
    panels.send_model_menu("token", "chat", "alpha::one", "alpha", request_context=context, provider_policy=policy)
    callbacks_seen = [button["callback_data"] for row in sent[0]["inline_keyboard"] for button in row]
    for action in ("test", "refresh", "reset"):
        assert any(data.startswith(f"provider:maint:{action}:") for data in callbacks_seen)


def test_cached_report_cannot_be_read_by_another_actor_or_session(context):
    module = tokens()
    report = module.ProviderReport((("alpha", "Alpha", "catalog reachable"),), 1000)
    token = module.store_provider_report(report, "chat", request_context=context)
    assert module.load_provider_report(token, "chat", request_context=context) == report
    for wrong in (replace(context, actor_id="other"), replace(context, session_id="other")):
        assert module.load_provider_report(token, "chat", request_context=wrong) is None
    assert module.load_provider_report(token, "other-chat", request_context=context) is None


def test_extreme_provider_labels_still_fit_telegram_limits(context, monkeypatch):
    _, _, policy = setup()
    monkeypatch.setattr(
        panels,
        "provider_health_checks",
        lambda **kw: [(f"p{i}", "😀" * 4000, "inference stream opened (completion not validated)") for i in range(10)],
    )
    sent = []
    monkeypatch.setattr(panels, "send_panel_message", lambda token, chat, text, *a, **kw: sent.append(text))
    panels.send_provider_health_menu("token", "chat", 1, request_context=context, provider_policy=policy)
    assert len(sent[0].encode("utf-16-le")) // 2 <= 4096


def test_callbacks_use_application_probe_service_and_paging_does_not_probe(context, monkeypatch):
    calls, sent = [], []

    class Probes:
        def check(self, provider_id=None):
            calls.append(provider_id)
            return [(f"p{i}", f"Provider {i}", "catalog reachable") for i in range(6)]

    _, health, policy = setup()
    health.fail(health.begin("p0", "one"), ProviderRequestError("p0::one", "timeout"))
    monkeypatch.setattr(panels, "provider_health_checks", lambda **kw: pytest.fail("legacy probe path used"))
    monkeypatch.setattr(
        panels, "send_panel_message", lambda token, chat, text, markup, *a, **kw: sent.append((text, markup))
    )
    probes = Probes()
    handle(context, "provider:health", policy=policy, probes=probes)
    assert calls == [None]
    assert "Recent: timeout" in sent[-1][0]
    next_button = next(b for row in sent[-1][1]["inline_keyboard"] for b in row if b["text"].startswith("Next"))
    handle(context, next_button["callback_data"], policy=policy, probes=probes)
    assert calls == [None]
    assert len(sent[-1][0].encode("utf-16-le")) // 2 <= 4096


def test_targeted_health_forwards_exact_provider_to_application_service(context, monkeypatch):
    calls = []

    class Probes:
        def check(self, provider_id=None):
            calls.append(provider_id)
            return [(provider_id, "Alpha", "catalog reachable")]

    token = tokens().provider_action_token("test", "alpha", "chat", request_context=context)
    monkeypatch.setattr(panels, "provider_health_checks", lambda **kw: pytest.fail("legacy probe path used"))
    monkeypatch.setattr(panels, "send_panel_message", lambda *a, **kw: None)
    handle(context, f"provider:maint:test:{token}", probes=Probes())
    assert calls == ["alpha"]
