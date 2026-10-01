"""Circuit admission and fallback are bounded, explicit and driven by actual calls."""

from __future__ import annotations

import threading
import urllib.error
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_provider_runtime_health import Clock

import bridge.provider_errors as errors
import bridge.provider_port as port_module
from bridge.model_router import ModelRouter
from bridge.provider_errors import ProviderRequestError
from bridge.provider_execution_policy import ProviderExecutionPolicy
from bridge.provider_port import ProviderPort
from bridge.provider_runtime_health import ProviderRuntimeHealth
from bridge.token_usage_values import TokenUsage


def setup(**provider_options):
    clock = Clock()
    health = ProviderRuntimeHealth(clock=clock)
    catalog = {
        "alpha": {"models": ["one", "two"], **provider_options},
        "beta": {"models": ["other"]},
        "gamma": {"models": ["last"]},
    }
    return clock, health, ProviderExecutionPolicy(ModelRouter(lambda: catalog), health)


def fail(health, category="timeout", status=None, **kwargs):
    attempt = health.begin("alpha", "one")
    health.fail(attempt, ProviderRequestError("alpha::one", category, status, **kwargs))


def open_circuit(health):
    for _ in range(3):
        fail(health)


def test_three_transient_failures_open_sixty_second_cooldown():
    clock, health, _ = setup()
    open_circuit(health)
    assert health.snapshot("alpha").state == "cooldown"
    assert health.snapshot("alpha").cooldown_until == clock.now + 60
    with pytest.raises(ProviderRequestError) as error:
        health.begin("alpha", "two")
    assert error.value.blocked
    assert error.value.retry_after == 60
    assert health.snapshot("alpha").consecutive_failures == 3


def test_only_one_half_open_request_is_admitted_and_success_recovers():
    clock, health, _ = setup()
    open_circuit(health)
    clock.now += 60
    barrier = threading.Barrier(4)

    def acquire(_):
        barrier.wait(timeout=5)
        try:
            return health.begin("alpha", "one")
        except ProviderRequestError:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        attempts = [x for x in pool.map(acquire, range(4)) if x is not None]
    assert len(attempts) == 1
    assert health.snapshot("alpha").state == "half_open"
    health.succeed(attempts[0])
    assert health.snapshot("alpha").state == "healthy"
    health.cancel(health.begin("alpha", "two"))


def test_failed_probe_backs_off_and_cancelled_probe_releases_reservation():
    clock, health, _ = setup()
    open_circuit(health)
    clock.now += 60
    attempt = health.begin("alpha", "one")
    health.cancel(attempt)
    attempt = health.begin("alpha", "two")
    health.fail(attempt, ProviderRequestError("alpha::two", "network"))
    assert health.snapshot("alpha").cooldown_until == clock.now + 120
    for _ in range(8):
        clock.now = health.snapshot("alpha").cooldown_until
        fail(health)
    assert health.snapshot("alpha").cooldown_until - clock.now == 900


def test_old_success_cannot_close_a_circuit_opened_while_it_was_running():
    _, health, _ = setup()
    old = health.begin("alpha", "two")
    open_circuit(health)
    health.succeed(old)
    assert health.snapshot("alpha").state == "cooldown"


def test_rate_limit_honors_retry_after_without_three_failures():
    clock, health, _ = setup()
    fail(health, "rate_limit", 429, retry_after=37)
    assert health.snapshot("alpha").cooldown_until == clock.now + 37
    clock.now += 36
    with pytest.raises(ProviderRequestError) as error:
        health.begin("alpha", "two")
    assert error.value.retry_after == 1
    clock.now += 1
    health.succeed(health.begin("alpha", "two"))
    assert health.snapshot("alpha").state == "healthy"


def test_503_server_retry_hint_is_respected_immediately():
    clock, health, _ = setup()
    fail(health, "provider_unavailable", 503, retry_after=90)
    assert health.snapshot("alpha").cooldown_until == clock.now + 90


@pytest.mark.parametrize("category,status", [("authentication", 401), ("credits", 402)])
def test_operator_action_failures_do_not_immediately_reissue_requests(category, status):
    clock, health, _ = setup()
    fail(health, category, status)
    with pytest.raises(ProviderRequestError):
        health.begin("alpha", "two")
    clock.now += 300
    health.succeed(health.begin("alpha", "one"))
    assert health.snapshot("alpha").state == "healthy"


def test_model_404_blocks_only_that_model_and_rechecks_later():
    clock, health, _ = setup()
    fail(health, "model_unavailable", 404)
    with pytest.raises(ProviderRequestError):
        health.begin("alpha", "one")
    health.succeed(health.begin("alpha", "two"))
    assert health.snapshot("alpha", "one").state == "model_unavailable"
    clock.now += 300
    health.succeed(health.begin("alpha", "one"))
    assert health.snapshot("alpha", "one").state == "healthy"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("37", 37),
        ("0", 0),
        ("99999999999", 86400),
        ("-1", None),
        ("1.5", None),
        ("nan", None),
        ("inf", None),
        (None, None),
        ("secret", None),
        ("Thu, 01 Jan 1970 00:17:17 GMT", 37),
        ("Thu, 01 Jan 1970 00:00:00 GMT", 0),
    ],
)
def test_retry_after_parses_http_dates_and_integer_seconds(value, expected):
    assert hasattr(errors, "parse_retry_after"), "Retry-After parsing is missing"
    assert errors.parse_retry_after(value, now=1000) == expected


def test_port_preserves_http_retry_after_in_safe_error_and_runtime_state():
    _, health, policy = setup()

    def backend(*args, **kwargs):
        raise urllib.error.HTTPError("https://hidden.invalid", 429, "secret", {"Retry-After": "37"}, None)

    port = ProviderPort(backend, policy=policy)
    with pytest.raises(ProviderRequestError) as error:
        port.generate("", "alpha::one", [])
    assert error.value.retry_after == 37
    assert health.snapshot("alpha").cooldown_until == 1037
    assert "secret" not in str(error.value)


def test_utility_fallback_records_each_actual_model_and_never_forwards_primary_key():
    _, health, policy = setup(utility_fallbacks=["beta::other"])
    calls, events = [], []

    def backend(key, model, messages, **kwargs):
        calls.append((key, model))
        kwargs["usage_callback"](TokenUsage(3, 1, 4))
        if model == "alpha::one":
            raise ProviderRequestError(model, "provider_unavailable", 503)
        return "summary"

    port = ProviderPort(backend, usage_recorder=events.append, policy=policy).for_usage("chat", "session", "summary")
    assert port.generate("primary-secret", "one", []) == "summary"
    assert calls == [("primary-secret", "alpha::one"), ("", "beta::other")]
    assert [(e.model, e.status) for e in events] == [("alpha::one", "failed"), ("beta::other", "succeeded")]
    assert health.snapshot("alpha").state == "degraded"
    assert health.snapshot("beta").state == "healthy"


def test_story_fallback_requires_explicit_opt_in():
    _, _, policy = setup(utility_fallbacks=["beta::other"], story_fallbacks=["beta::other"])
    assert policy.candidates("one", "story") == ("alpha::one",)
    _, _, policy = setup(story_fallbacks=["beta::other"], allow_story_fallback=True)
    assert policy.candidates("one", "story") == ("alpha::one", "beta::other")


def test_fallback_candidates_are_exact_unique_known_and_bounded():
    _, _, policy = setup(
        utility_fallbacks=["other", "unknown::x", "alpha::one", "beta::other", "beta::other", "gamma::last"]
    )
    assert policy.candidates("one", "summary") == ("alpha::one", "beta::other", "gamma::last")


def test_unknown_purpose_and_language_rendering_never_inherit_utility_fallback():
    _, _, policy = setup(utility_fallbacks=["beta::other"])
    for purpose in ("", "render", "language", "humanizer", "story", "image", "edit"):
        assert policy.candidates("one", purpose) == ("alpha::one",)


def test_circuit_block_is_not_counted_as_a_remote_attempt_or_usage():
    _, health, policy = setup(utility_fallbacks=["beta::other"])
    open_circuit(health)
    events, calls = [], []
    port = ProviderPort(
        lambda key, model, messages, **kw: calls.append(model) or "ok", policy=policy, usage_recorder=events.append
    ).for_usage("c", "s", "summary")
    assert port.generate("", "one", []) == "ok"
    assert calls == ["beta::other"]
    assert [e.model for e in events] == ["beta::other"]
    assert health.snapshot("alpha").consecutive_failures == 3


def test_fallback_never_runs_after_visible_stream_output():
    _, _health, policy = setup(utility_fallbacks=["beta::other"])
    calls, visible = [], []

    def backend(key, model, messages, **kwargs):
        calls.append(model)
        kwargs["stream_callback"]("partial")
        raise TimeoutError()

    port = ProviderPort(backend, policy=policy).for_usage("c", "s", "summary")
    with pytest.raises(ProviderRequestError):
        port.generate("", "one", [], stream_callback=visible.append)
    assert calls == ["alpha::one"]
    assert visible == ["partial"]


def test_local_stream_callback_failure_is_not_provider_network_failure():
    _, health, policy = setup(utility_fallbacks=["beta::other"])

    def backend(*args, **kwargs):
        kwargs["stream_callback"]("visible")
        return "visible"

    def broken_callback(text):
        raise OSError("local delivery failure")

    with pytest.raises(OSError):
        ProviderPort(backend, policy=policy).generate("", "one", [], stream_callback=broken_callback)
    assert health.snapshot("alpha").state == "unknown"


def test_pre_cancelled_call_does_not_contact_a_provider():
    _, health, policy = setup(utility_fallbacks=["beta::other"])
    cancelled = threading.Event()
    cancelled.set()
    calls = []
    port = ProviderPort(lambda *a, **kw: calls.append(a) or "ok", policy=policy)
    with pytest.raises(RuntimeError, match="cancel"):
        port.generate("", "one", [], cancel_event=cancelled)
    assert not calls
    assert health.snapshot("alpha").state == "unknown"


def test_cancellation_during_failure_never_falls_back_or_penalizes_provider():
    _, health, policy = setup(utility_fallbacks=["beta::other"])
    cancelled, calls = threading.Event(), []

    def backend(key, model, messages, **kwargs):
        calls.append(model)
        cancelled.set()
        raise TimeoutError()

    with pytest.raises(ProviderRequestError):
        ProviderPort(backend, policy=policy).for_usage("c", "s", "summary").generate(
            "", "one", [], cancel_event=cancelled
        )
    assert calls == ["alpha::one"]
    assert health.snapshot("alpha").state == "unknown"


def test_total_request_deadline_is_not_restarted_for_fallback(monkeypatch):
    clock, _, policy = setup(utility_fallbacks=["beta::other"])
    monkeypatch.setattr(port_module.time, "monotonic", clock)
    calls = []

    def backend(key, model, messages, **kwargs):
        calls.append(model)
        clock.now += 4
        raise TimeoutError()

    with pytest.raises(ProviderRequestError):
        ProviderPort(backend, policy=policy).for_usage("c", "s", "summary").generate("", "one", [], request_timeout=3)
    assert calls == ["alpha::one"]


@pytest.mark.parametrize("category", ["request_too_large", "provider_rejected"])
def test_request_local_errors_never_trigger_fallback(category):
    _, health, policy = setup(utility_fallbacks=["beta::other"])
    calls = []

    def backend(key, model, messages, **kwargs):
        calls.append(model)
        raise ProviderRequestError(model, category, 400)

    with pytest.raises(ProviderRequestError):
        ProviderPort(backend, policy=policy).for_usage("c", "s", "summary").generate("", "one", [])
    assert calls == ["alpha::one"]
    assert health.snapshot("alpha").state == "unknown"


def test_late_transient_failure_cannot_replace_newer_authentication_block():
    _, health, _ = setup()
    old = health.begin("alpha", "two")
    fail(health, "authentication", 401)
    health.fail(old, ProviderRequestError("alpha::two", "timeout"))
    assert health.snapshot("alpha").state == "auth_error"
    with pytest.raises(ProviderRequestError) as error:
        health.begin("alpha", "one")
    assert error.value.category == "authentication"


def test_existing_parallel_failures_still_trip_at_three():
    _, health, _ = setup()
    attempts = [health.begin("alpha", "one") for _ in range(3)]
    for attempt in attempts:
        health.fail(attempt, ProviderRequestError("alpha::one", "timeout"))
    assert health.snapshot("alpha").state == "cooldown"
    assert health.snapshot("alpha").consecutive_failures == 3


def test_utility_fallback_stops_after_two_distinct_alternatives():
    _, _, policy = setup(utility_fallbacks=["beta::other", "gamma::last", "alpha::two"])
    calls = []

    def backend(key, model, messages, **kwargs):
        calls.append(model)
        raise TimeoutError()

    with pytest.raises(ProviderRequestError):
        ProviderPort(backend, policy=policy).for_usage("c", "s", "summary").generate("", "one", [])
    assert calls == ["alpha::one", "beta::other", "gamma::last"]


def test_concurrent_rate_limits_keep_the_longest_server_retry_hint():
    clock, health, _ = setup()
    first = health.begin("alpha", "one")
    second = health.begin("alpha", "two")
    health.fail(first, ProviderRequestError("alpha::one", "rate_limit", 429, retry_after=37))
    health.fail(second, ProviderRequestError("alpha::two", "rate_limit", 429, retry_after=120))
    assert health.snapshot("alpha").cooldown_until == clock.now + 120


def test_cancelled_failure_is_recorded_as_cancelled_usage_not_provider_failure():
    _, health, policy = setup()
    events = []
    cancelled = threading.Event()

    def backend(*args, **kwargs):
        cancelled.set()
        raise TimeoutError()

    port = ProviderPort(backend, policy=policy, usage_recorder=events.append).for_usage("c", "s", "story")
    with pytest.raises(ProviderRequestError):
        port.generate("", "one", [], cancel_event=cancelled)
    assert events[0].status == "cancelled"
    assert health.snapshot("alpha").state == "unknown"
