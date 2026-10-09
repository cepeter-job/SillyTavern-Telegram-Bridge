"""Opt-in observation cannot alter requests or retain private source text."""

import copy
import importlib.util
import json
from dataclasses import replace

import pytest

from bridge.memory_contracts import MemoryPromptContext, MemoryReadScope
from bridge.token_usage_values import TokenUsage, UsageScope


def runtime(**kwargs):
    assert importlib.util.find_spec("bridge.request_observation"), "request observer not implemented"
    from bridge.request_observation import RequestObservationRuntime

    return RequestObservationRuntime(**kwargs)


def prompt():
    scope = MemoryReadScope("PRIVATE_CHAT", "PRIVATE_SESSION", 1.0, 20, 0, ("PRIVATE_READER",))
    messages = [
        {
            "role": "system",
            "content": "PRIVATE_STABLE_INSTRUCTION " * 24,
            "_context_selection": MemoryPromptContext(scope=scope),
        },
        {"role": "user", "content": "PRIVATE_USER_REFUSAL. Do not borrow my compass."},
    ]
    session = {"session_id": "PRIVATE_SESSION", "character_file": "PRIVATE_CARD", "persona_id": "PRIVATE_PERSONA"}
    return messages, session


DEFAULT_USAGE = TokenUsage(100, 10, 110, 40)


def observed(
    obs,
    *,
    text="PRIVATE_CURRENT",
    usage=DEFAULT_USAGE,
    phase="initial",
    fail=False,
    identity=True,
    purpose="story",
    selection="p::m",
    route="https://example.test/v1",
):
    from bridge.request_observation_context import logical_observation, note_observed_usage, observed_request

    messages, session = prompt()
    profile = obs.profile_identity(messages, session) if identity else None
    request = {"model": "m", "messages": [{k: v for k, v in m.items() if not k.startswith("_")} for m in messages]}
    request["messages"][-1]["content"] = text
    original = copy.deepcopy(request)
    scope = UsageScope("PRIVATE_CHAT", "PRIVATE_SESSION", purpose)
    with logical_observation(obs, scope, profile):
        with observed_request(
            request,
            request["messages"],
            selection=selection,
            route=route,
            transport="chat_completions",
            phase=phase,
            request_bytes=len(json.dumps(request).encode()),
        ):
            if usage is not None:
                note_observed_usage(usage)
            if fail:
                raise TimeoutError("PRIVATE_BACKEND_ERROR")
    assert request == original
    return request


def test_opt_in_is_explicit():
    runtime()
    from bridge.request_observation import build_request_observer

    assert build_request_observer({}) is None
    assert build_request_observer({"SILLYTAVERN_REQUEST_OBSERVATION": "off"}) is None
    with pytest.raises(ValueError):
        build_request_observer({"SILLYTAVERN_REQUEST_OBSERVATION": "maybe"})
    assert build_request_observer({"SILLYTAVERN_REQUEST_OBSERVATION": "on"}) is not None


def test_real_requests_are_unchanged_and_metadata_is_content_free():
    obs = runtime(emit=lambda fields: None)
    for i in range(3):
        observed(obs, text=f"PRIVATE_TEXT_{i}")
    report = obs.report()
    assert report["requests_started"] == report["requests_finished"] == 3
    assert report["window_usage"]["known_input_tokens"] == 300
    assert report["window_usage"]["known_cached_tokens"] == 120
    assert report["window_usage"]["complete_input_tokens"] == 300
    assert report["records"][-1]["stable_prefix_characters"] > 0
    assert report["records"][-1]["prefix_scope"] == "native_prompt"
    assert "PRIVATE" not in json.dumps(report)
    assert "PRIVATE" not in repr(obs)
    assert report["optimization_authorized"] is False
    assert report["accepted_work_savings_fraction"] is None


def test_unknown_and_failed_usage_are_not_zero_or_skipped():
    obs = runtime(emit=lambda fields: None)
    observed(obs, usage=TokenUsage(0, 0, 0, 0))
    observed(obs, usage=None)
    with pytest.raises(TimeoutError):
        observed(obs, usage=None, fail=True, phase="continuation")
    report = obs.report()
    assert report["requests_finished"] == 3
    assert report["window_usage"]["unknown_input_requests"] == 2
    assert report["window_usage"]["known_input_tokens"] == 0
    assert report["window_usage"]["complete_input_tokens"] is None
    assert report["records"][-1]["status"] == "failed"
    assert report["records"][-1]["phase"] == "continuation"
    assert "PRIVATE_BACKEND_ERROR" not in json.dumps(report)


def test_missing_scope_counts_usage_but_never_claims_prefix_stability():
    obs = runtime(emit=lambda fields: None)
    for _ in range(3):
        observed(obs, identity=False)
    report = obs.report()
    assert report["window_usage"]["known_input_tokens"] == 300
    assert report["prefix_profile"]["cohorts"] == []
    assert all(r["prefix_scope"] == "unavailable" for r in report["records"])


def test_route_purpose_reader_and_revision_are_separate():
    obs = runtime(emit=lambda fields: None)
    observed(obs)
    observed(obs, purpose="summary")
    observed(obs, selection="p::other")
    observed(obs, route="https://another.test/v1")
    assert len(obs.report()["prefix_profile"]["cohorts"]) == 4
    messages, session = prompt()
    first = obs.profile_identity(messages, session)
    memory = messages[0]["_context_selection"].scope
    for changes in ({"principals": ("other",)}, {"rewrite_revision": 1}, {"session_created_at": 2.0}):
        messages[0]["_context_selection"] = MemoryPromptContext(scope=replace(memory, **changes))
        assert obs.profile_identity(messages, session) != first


def test_recent_record_and_prefix_windows_are_bounded():
    obs = runtime(record_limit=8, window=4, emit=lambda fields: None)
    for i in range(40):
        observed(obs, text=f"PRIVATE_{i}")
    report = obs.report()
    assert len(report["records"]) == 8
    assert report["requests_finished"] == 40
    assert report["evicted_records"] == 32
    assert report["prefix_profile"]["cohorts"][0]["retained_samples"] == 4
    assert report["active_requests"] == 0


def test_logging_failure_cannot_break_generation():
    def broken(fields):
        raise RuntimeError("PRIVATE_LOG_SINK")

    obs = runtime(emit=broken)
    observed(obs)
    assert obs.report()["requests_finished"] == 1
    assert obs.report()["observer_errors"] == 1
    assert "PRIVATE" not in json.dumps(obs.report())


def test_observer_failure_does_not_swallow_the_backend_error():
    runtime()
    from bridge.request_observation_context import logical_observation, observed_request

    class Broken:
        def begin(self, *args, **kwargs):
            raise RuntimeError("PRIVATE_OBSERVER")

    with logical_observation(Broken(), None, None):
        with pytest.raises(TimeoutError, match="original"):
            with observed_request(
                {}, [], selection="p::m", route="", transport="chat_completions", phase="initial", request_bytes=2
            ):
                raise TimeoutError("original")


def test_overlap_and_nested_requests_cannot_swap_usage():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from bridge.request_observation_context import logical_observation, note_observed_usage, observed_request

    obs = runtime(emit=lambda fields: None)
    barrier = Barrier(2)

    def worker(n):
        with logical_observation(obs, UsageScope("c", "s", "summary"), None):
            with observed_request(
                {},
                [],
                selection=f"p::{n}",
                route="https://example.test",
                transport="chat_completions",
                phase="initial",
                request_bytes=n,
            ):
                barrier.wait(timeout=5)
                note_observed_usage(TokenUsage(n, 1, n + 1))

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(worker, [10, 20]))
    assert sorted((r["bytes"], r["input_tokens"]) for r in obs.report()["records"]) == [(10, 10), (20, 20)]
    assert obs.report()["active_requests"] == 0


def test_scope_ownership_mismatch_refuses_prefix_comparison():
    from bridge.request_observation_context import logical_observation, observed_request

    obs = runtime(emit=lambda fields: None)
    messages, session = prompt()
    identity = obs.profile_identity(messages, session)
    wire = [{k: v for k, v in m.items() if not k.startswith("_")} for m in messages]
    with logical_observation(obs, UsageScope("OTHER", "OTHER", "story"), identity):
        with observed_request(
            {"messages": wire},
            wire,
            selection="p::m",
            route="x",
            transport="chat_completions",
            phase="initial",
            request_bytes=100,
        ):
            pass
    assert obs.report()["records"][0]["prefix_scope"] == "unavailable"


def test_multiple_usage_captures_are_unknown_not_double_counted():
    from bridge.request_observation_context import logical_observation, note_observed_usage, observed_request

    obs = runtime(emit=lambda fields: None)
    with logical_observation(obs, None, None):
        with observed_request(
            {}, [], selection="m", route="x", transport="chat_completions", phase="initial", request_bytes=2
        ):
            note_observed_usage(TokenUsage(10, 2, 12))
            note_observed_usage(TokenUsage(20, 2, 22))
    row = obs.report()["records"][0]
    assert row["usage_readings"] == 2
    assert row["input_tokens"] is None and not row["usage_complete"]
