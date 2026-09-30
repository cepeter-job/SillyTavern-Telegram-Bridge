"""Real request observations remain isolated from discovery and session selection."""

from __future__ import annotations

import importlib
import importlib.util
import threading
import urllib.error
from dataclasses import FrozenInstanceError

import pytest

from bridge.model_router import ModelRouter
from bridge.provider_errors import ProviderRequestError
from bridge.provider_port import ProviderPort
from bridge.settings import load_app_settings


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def monitor(clock=None):
    name = "bridge.provider_runtime_health"
    assert importlib.util.find_spec(name) is not None, "runtime provider-health service is missing"
    return importlib.import_module(name).ProviderRuntimeHealth(clock=clock or Clock())


def policy(health):
    name = "bridge.provider_execution_policy"
    assert importlib.util.find_spec(name) is not None, "provider execution policy is missing"
    router = ModelRouter(lambda: {"alpha": {"models": ["one", "two"]}, "beta": {"models": ["other"]}})
    return importlib.import_module(name).ProviderExecutionPolicy(router, health)


def fail(health, category="timeout", model="one", provider="alpha", status=None):
    attempt = health.begin(provider, model)
    health.fail(attempt, ProviderRequestError(f"{provider}::{model}", category, status))


def test_new_provider_is_unknown_not_healthy():
    health = monitor()
    snapshot = health.snapshot("alpha")
    assert snapshot.state == "unknown"
    assert snapshot.last_success_at is None
    assert snapshot.last_failure_at is None
    with pytest.raises(FrozenInstanceError):
        snapshot.state = "healthy"


def test_actual_success_records_provider_and_model():
    clock = Clock()
    health = monitor(clock)
    health.succeed(health.begin("alpha", "one"))
    assert health.snapshot("alpha").state == "healthy"
    assert health.snapshot("alpha", "one").last_success_at == clock.now
    assert health.snapshot("beta").state == "unknown"


@pytest.mark.parametrize("category,status", [("timeout", None), ("network", None), ("provider_unavailable", 503)])
def test_transient_errors_update_only_the_selected_provider(category, status):
    health = monitor()
    fail(health, category, status=status)
    snapshot = health.snapshot("alpha")
    assert snapshot.state == "degraded"
    assert snapshot.consecutive_failures == 1
    assert snapshot.last_category == category
    assert snapshot.last_status == status
    assert health.snapshot("beta").state == "unknown"


@pytest.mark.parametrize(
    "category,status,state",
    [("authentication", 401, "auth_error"), ("credits", 402, "credits_required"), ("rate_limit", 429, "rate_limited")],
)
def test_account_failures_have_distinct_states(category, status, state):
    health = monitor()
    fail(health, category, status=status)
    assert health.snapshot("alpha").state == state


def test_model_404_does_not_disable_its_siblings_or_provider():
    health = monitor()
    fail(health, "model_unavailable", status=404)
    assert health.snapshot("alpha", "one").state == "model_unavailable"
    assert health.snapshot("alpha", "two").state == "unknown"
    assert health.snapshot("alpha").state == "unknown"


@pytest.mark.parametrize("category,status", [("request_too_large", 413), ("provider_rejected", 400)])
def test_request_local_failures_do_not_punish_provider(category, status):
    health = monitor()
    fail(health, category, status=status)
    assert health.snapshot("alpha").state == "unknown"
    assert health.snapshot("alpha", "one").state == "unknown"


def test_success_resets_streak_but_preserves_last_error_metadata():
    health = monitor()
    fail(health)
    health.succeed(health.begin("alpha", "one"))
    snapshot = health.snapshot("alpha")
    assert snapshot.state == "healthy"
    assert snapshot.consecutive_failures == 0
    assert snapshot.last_category == "timeout"


def test_older_success_cannot_erase_a_newer_failure():
    health = monitor()
    older = health.begin("alpha", "one")
    fail(health)
    health.succeed(older)
    assert health.snapshot("alpha").state == "degraded"
    assert health.snapshot("alpha").consecutive_failures == 1


def test_repeated_completion_is_idempotent_and_cancel_is_not_failure():
    health = monitor()
    attempt = health.begin("alpha", "one")
    health.cancel(attempt)
    health.fail(attempt, ProviderRequestError("alpha::one", "timeout"))
    assert health.snapshot("alpha").state == "unknown"
    attempt = health.begin("alpha", "one")
    health.fail(attempt, ProviderRequestError("alpha::one", "timeout"))
    health.fail(attempt, ProviderRequestError("alpha::one", "timeout"))
    assert health.snapshot("alpha").consecutive_failures == 1


def test_provider_port_observes_canonical_route_and_preserves_usage_identity():
    health = monitor()
    events = []
    port = ProviderPort(lambda *a, **k: "visible", usage_recorder=events.append, policy=policy(health))
    assert port.for_usage("chat", "session", "story").generate("", "one", []) == "visible"
    assert health.snapshot("alpha", "one").state == "healthy"
    assert events[0].model == "alpha::one"


def test_port_normalizes_failure_without_retaining_private_error_body():
    health = monitor()

    def backend(*args, **kwargs):
        raise urllib.error.HTTPError("https://private.invalid/secret", 503, "private-body", {}, None)

    port = ProviderPort(backend, policy=policy(health))
    with pytest.raises(ProviderRequestError) as error:
        port.generate("private-key", "alpha::one", [])
    assert health.snapshot("alpha").last_category == "provider_unavailable"
    assert "private" not in str(error.value)
    assert "private" not in repr(health.snapshot("alpha"))


def test_cancelled_response_does_not_mark_provider_healthy():
    health = monitor()
    cancelled = threading.Event()

    def backend(*args, **kwargs):
        cancelled.set()
        return "partial"

    port = ProviderPort(backend, policy=policy(health))
    assert port.generate("", "alpha::one", [], cancel_event=cancelled) == "partial"
    assert health.snapshot("alpha").state == "unknown"


def test_startup_composes_independent_monitors_without_network(tmp_path):
    from bridge.main import _build_startup_services

    settings = load_app_settings({}, home=tmp_path)
    router = ModelRouter(lambda: {"alpha": {"models": ["one"]}})
    first = _build_startup_services(settings, model_router=router)
    second = _build_startup_services(settings, model_router=router)
    assert hasattr(first.provider, "policy"), "startup does not compose runtime observation"
    assert first.provider.policy is not None
    assert first.provider.policy.health is not second.provider.policy.health
