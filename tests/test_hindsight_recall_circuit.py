"""Endpoint health generations prevent stale requests from undoing recovery."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from hindsight_recall_test_support import Client, Clock, Gate, StatusError, recall, response, runtime_for


@pytest.mark.parametrize("failure", [ConnectionError(), TimeoutError(), StatusError(429), StatusError(503)])
def test_availability_failure_bypasses_until_one_successful_probe(failure):
    clock = Clock()
    probe = Gate(result=response())
    attempts = []

    async def behavior(kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise failure
        if len(attempts) == 2:
            return await probe.wait()
        return response(("recovered", "world"))

    runtime = runtime_for(Client(behavior), clock=clock)
    runtime.start()
    try:
        assert recall(runtime) == ()
        for _ in range(5):
            assert recall(runtime) == ()
        assert len(attempts) == 1
        clock.advance(31)
        with ThreadPoolExecutor(max_workers=1) as callers:
            future = callers.submit(recall, runtime, "probe")
            try:
                assert probe.entered.wait(0.5)
                assert recall(runtime, "parallel probe") == ()
                assert len(attempts) == 2
            finally:
                probe.release()
            assert future.result(timeout=0.5) == ()
        assert recall(runtime) == (("recovered", "world"),)
    finally:
        probe.release()
        assert runtime.close()


def test_failed_probe_opens_a_fresh_cooldown():
    clock = Clock()

    async def behavior(_kwargs):
        raise StatusError(503)

    client = Client(behavior)
    runtime = runtime_for(client, clock=clock)
    runtime.start()
    try:
        assert recall(runtime) == ()
        clock.advance(31)
        assert recall(runtime) == ()
        clock.advance(29)
        assert recall(runtime) == ()
        assert len(client.calls) == 2
        clock.advance(2)
        assert recall(runtime) == ()
        assert len(client.calls) == 3
    finally:
        assert runtime.close()


def test_missing_bank_is_empty_success_without_endpoint_penalty():
    async def behavior(kwargs):
        if kwargs["query"] == "new bank":
            raise StatusError(404)
        return response(("existing", "world"))

    runtime = runtime_for(Client(behavior))
    runtime.start()
    try:
        assert recall(runtime, "new bank") == ()
        assert recall(runtime, "existing bank") == (("existing", "world"),)
    finally:
        assert runtime.close()


@pytest.mark.parametrize("failure", [StatusError(401), StatusError(403), ValueError("invalid response")])
def test_configuration_failure_is_rate_limited_without_logging_remote_detail(failure, caplog):
    async def behavior(_kwargs):
        raise failure

    client = Client(behavior)
    runtime = runtime_for(client)
    runtime.start()
    try:
        for _ in range(5):
            assert recall(runtime) == ()
        assert len(client.calls) == 1
    finally:
        assert runtime.close()
    assert "configuration" in caplog.text
    assert "invalid response" not in caplog.text


def test_old_success_cannot_clear_newer_outage():
    old = Gate()
    clock = Clock()

    async def behavior(kwargs):
        if kwargs["query"] == "old":
            return await old.wait()
        raise ConnectionError()

    client = Client(behavior)
    runtime = runtime_for(client, clock=clock, timeout_seconds=60)
    runtime.start()
    with ThreadPoolExecutor(max_workers=1) as callers:
        future = callers.submit(recall, runtime, "old")
        try:
            assert old.entered.wait(0.5)
            assert recall(runtime, "outage") == ()
            old.release()
            assert future.result(timeout=0.5)
            assert recall(runtime, "still cooling") == ()
            assert len(client.calls) == 2
        finally:
            old.release()
            assert runtime.close()


def test_stale_failure_cannot_reopen_after_newer_probe_succeeds():
    old = Gate()
    clock = Clock()

    async def behavior(kwargs):
        if kwargs["query"] == "old":
            await old.wait()
            raise ConnectionError()
        if kwargs["query"] == "outage":
            raise ConnectionError()
        return response(("recovered", "experience"))

    client = Client(behavior)
    runtime = runtime_for(client, clock=clock, timeout_seconds=60)
    runtime.start()
    with ThreadPoolExecutor(max_workers=1) as callers:
        future = callers.submit(recall, runtime, "old")
        try:
            assert old.entered.wait(0.5)
            assert recall(runtime, "outage") == ()
            clock.advance(31)
            assert recall(runtime, "probe") == (("recovered", "experience"),)
            old.release()
            assert future.result(timeout=0.5) == ()
            assert recall(runtime, "normal") == (("recovered", "experience"),)
            assert len(client.calls) == 4
        finally:
            old.release()
            assert runtime.close()
