"""Caller deadlines and true task ownership bound optional semantic recall."""

import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import ClassVar

import aiohttp
import pytest
from hindsight_recall_test_support import Client, Clock, Gate, recall, response, runtime_for


def test_runtime_reuses_one_sdk_transport_and_closes_owner_loop(monkeypatch):
    from bridge.hindsight_recall_runtime import HindsightRecallRuntime

    transports = []
    requests = []

    class HttpResponse:
        status = 200
        reason = "OK"
        headers: ClassVar = {"Content-Type": "application/json"}

        async def read(self):
            return b'{"results":[{"id":"one","document_id":"native-one","text":"REMOTE","type":"world"}]}'

    async def http_request(session, method, url, **kwargs):
        transports.append((session, session.connector, asyncio.get_running_loop(), threading.current_thread()))
        requests.append(json.loads(kwargs["data"]))
        return HttpResponse()

    monkeypatch.setattr(aiohttp.ClientSession, "_request", http_request)
    runtime = HindsightRecallRuntime(base_url="http://127.0.0.1:8888", api_key=None)
    assert recall(runtime) == ()
    assert transports == []
    runtime.start()
    try:
        assert recall(runtime) == (("native-one", "world"),)
        assert recall(runtime) == (("native-one", "world"),)
        assert len(transports) == 2
        assert transports[0] == transports[1]
        assert requests[0]["tags"] == ["session:story", "native-fact"]
        assert requests[0]["tags_match"] == "all_strict"
        assert requests[0]["types"] == ["world", "experience"]
        assert not any(requests[0]["include"].get(key) for key in ("entities", "chunks", "source_facts"))
    finally:
        assert runtime.close()
    session, connector, loop, owner = transports[0]
    assert session.closed and connector.closed and loop.is_closed()
    assert owner.name == "st-hindsight-recall" and not owner.daemon and not owner.is_alive()
    runtime.start()
    assert recall(runtime) == ()
    assert runtime.close()
    assert len(transports) == 2


def test_runtime_constructs_client_on_owner_loop_with_foreground_policy():
    from bridge.hindsight_recall_runtime import HindsightRecallRuntime

    calls = []
    client = Client()

    def factory(**kwargs):
        calls.append((kwargs, asyncio.get_running_loop(), threading.current_thread()))
        return client

    runtime = HindsightRecallRuntime(base_url="http://127.0.0.1:8888", api_key="synthetic", client_factory=factory)
    assert calls == []
    runtime.start()
    runtime.start()
    try:
        assert recall(runtime)
    finally:
        assert runtime.close()
    assert len(calls) == 1
    assert calls[0][0] == {
        "base_url": "http://127.0.0.1:8888",
        "api_key": "synthetic",
        "timeout": 1.5,
        "max_attempts": 1,
        "user_agent": "SillyTavernTelegramBridge/1.0",
    }
    assert all(loop is calls[0][1] for loop in client.loops)
    assert client.closed


def test_caller_deadline_does_not_wait_for_cancellation_or_accept_late_ids():
    gate = Gate(resistant=True)
    client = Client(lambda _kwargs: gate.wait())
    runtime = runtime_for(client, timeout_seconds=0.08)
    runtime.start()
    try:
        started = time.monotonic()
        assert recall(runtime) == ()
        assert time.monotonic() - started < 0.5
        assert gate.cancelled.wait(0.5)
        assert not gate.finished.is_set()
        gate.release()
        assert gate.finished.wait(0.5)
        assert recall(runtime) == ()  # The timeout opens endpoint cooldown, not a cached late result.
        assert len(client.calls) == 1
    finally:
        gate.release()
        assert runtime.close()


def test_capacity_bypass_does_not_open_circuit():
    gates = {key: Gate() for key in ("first", "second")}

    async def behavior(kwargs):
        return await gates[kwargs["query"]].wait() if kwargs["query"] in gates else response(("next", "experience"))

    client = Client(behavior)
    runtime = runtime_for(client, timeout_seconds=1.0)
    runtime.start()
    with ThreadPoolExecutor(max_workers=2) as callers:
        futures = [callers.submit(recall, runtime, key) for key in gates]
        try:
            assert all(gate.entered.wait(0.5) for gate in gates.values())
            started = time.monotonic()
            assert recall(runtime, "overload") == ()
            assert time.monotonic() - started < 0.2
            assert len(client.calls) == 2
            for gate in gates.values():
                gate.release()
            assert all(future.result(timeout=0.5) for future in futures)
            assert recall(runtime, "next") == (("next", "experience"),)
        finally:
            for gate in gates.values():
                gate.release()
            assert runtime.close()


def test_stuck_tasks_keep_capacity_and_close_truthfully_reports_failure():
    clock = Clock()
    gates = {key: Gate(resistant=True) for key in ("first", "second")}
    client = Client(lambda kwargs: gates[kwargs["query"]].wait())
    runtime = runtime_for(client, timeout_seconds=0.08, clock=clock)
    runtime.start()
    with ThreadPoolExecutor(max_workers=2) as callers:
        futures = [callers.submit(recall, runtime, key) for key in gates]
        try:
            assert all(gate.entered.wait(0.5) for gate in gates.values())
            assert [future.result(timeout=0.5) for future in futures] == [(), ()]
            clock.advance(31)
            for _ in range(20):
                assert recall(runtime, "no replacement") == ()
            assert len(client.calls) == 2
            started = time.monotonic()
            assert not runtime.close(timeout=0.03)
            assert time.monotonic() - started < 0.3
            assert not client.closed
            runtime.start()
            assert len({loop for loop in client.loops}) == 1
        finally:
            for gate in gates.values():
                gate.release()
            assert runtime.close(timeout=1.0)


def test_shutdown_wakes_waiting_callers_before_slow_task_finishes():
    gate = Gate(resistant=True)
    runtime = runtime_for(Client(lambda _kwargs: gate.wait()), timeout_seconds=5)
    runtime.start()
    with ThreadPoolExecutor(max_workers=1) as callers:
        future = callers.submit(recall, runtime)
        try:
            assert gate.entered.wait(0.5)
            runtime.begin_shutdown()
            assert future.result(timeout=0.2) == ()
            assert recall(runtime) == ()
            assert not runtime.close(timeout=0.01)
        finally:
            gate.release()
            assert runtime.close(timeout=1)


def test_result_boundary_discards_remote_prose_unknown_types_and_excess_results():
    async def behavior(_kwargs):
        return response(("observation", "observation"), ("", "world"), *((f"native-{i}", "world") for i in range(100)))

    client = Client(behavior)
    runtime = runtime_for(client)
    runtime.start()
    try:
        result = recall(runtime, "q" * 8000)
        assert 0 < len(result) <= 64
        assert result[0] == ("native-0", "world")
        assert all(len(item) == 2 and item[1] == "world" for item in result)
        assert len(client.calls[0]["query"]) == 4000
    finally:
        assert runtime.close()


@pytest.mark.parametrize("failure", [ImportError, ValueError])
def test_startup_factory_failure_is_local_only_and_never_restarts(failure):
    from bridge.hindsight_recall_runtime import HindsightRecallRuntime

    calls = []

    def factory(**kwargs):
        calls.append(kwargs)
        raise failure("synthetic failure")

    runtime = HindsightRecallRuntime(base_url="http://127.0.0.1:8888", api_key=None, client_factory=factory)
    runtime.start()
    assert recall(runtime) == ()
    assert runtime.close()
    runtime.start()
    assert len(calls) == 1


def test_sdk_close_failure_cannot_report_successful_shutdown():
    class BrokenClose(Client):
        async def aclose(self):
            raise RuntimeError("synthetic close failure")

    runtime = runtime_for(BrokenClose())
    runtime.start()
    assert recall(runtime)
    assert not runtime.close()
    assert not runtime.close()


def test_cancel_before_task_installation_never_calls_sdk_or_leaks_admission():
    client = Client()
    runtime = runtime_for(client, timeout_seconds=0.08)
    runtime.start()
    entered, release = threading.Event(), threading.Event()

    def block_owner():
        entered.set()
        release.wait(1)

    try:
        assert recall(runtime)
        client.loops[0].call_soon_threadsafe(block_owner)
        assert entered.wait(0.5)
        started = time.monotonic()
        assert recall(runtime, "never dispatched") == ()
        assert time.monotonic() - started < 0.4
        runtime.begin_shutdown()
    finally:
        release.set()
        assert runtime.close()
    assert len(client.calls) == 1


@pytest.mark.parametrize("status", [429, 503])
def test_installed_sdk_capacity_response_is_not_retried(monkeypatch, status):
    from bridge.hindsight_recall_runtime import HindsightRecallRuntime

    requests = []

    class HttpResponse:
        reason = "At capacity"
        headers: ClassVar = {"Content-Type": "application/json", "Retry-After": "0"}

        async def read(self):
            return b'{"detail":"at capacity"}'

    response = HttpResponse()
    response.status = status

    async def http_request(session, method, url, **kwargs):
        requests.append(url)
        return response

    monkeypatch.setattr(aiohttp.ClientSession, "_request", http_request)
    runtime = HindsightRecallRuntime(base_url="http://127.0.0.1:8888", api_key=None)
    runtime.start()
    try:
        assert recall(runtime) == ()
        assert recall(runtime) == ()
        assert len(requests) == 1
    finally:
        assert runtime.close()
