"""Slow SDK initialization must not cancel recall or escape shutdown ownership."""

import asyncio
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from hindsight_recall_test_support import Client, recall

from bridge.hindsight_recall_runtime import HindsightRecallRuntime


class StartingClient(Client):
    def __init__(self):
        super().__init__()
        self.close_calls = 0

    async def aclose(self):
        self.close_calls += 1
        await super().aclose()


def test_slow_initialization_becomes_ready_without_restarting_owner(caplog):
    entered, release = threading.Event(), threading.Event()
    client = StartingClient()
    owners = []

    def factory(**kwargs):
        owners.append((threading.current_thread(), asyncio.get_running_loop(), kwargs))
        entered.set()
        assert release.wait(2)
        return client

    runtime = HindsightRecallRuntime(
        base_url="http://127.0.0.1:8888", api_key="sensitive-api-key", timeout_seconds=0.04, client_factory=factory
    )
    caplog.set_level(logging.INFO)
    try:
        started = time.monotonic()
        runtime.start()
        assert time.monotonic() - started < 0.5
        assert entered.wait(0.5)
        assert recall(runtime) == ()
        assert client.calls == []
        assert "Optional Hindsight recall ready" not in caplog.messages
        runtime.start()
        assert len(owners) == 1
        release.set()
        assert runtime._started.wait(1)
        assert recall(runtime, "sensitive-story-query") == (("native-document", "world"),)
        assert caplog.messages.count("Optional Hindsight recall ready") == 1
        assert "Optional Hindsight recall initialization continuing in background" in caplog.messages
        assert all(record.levelno == logging.INFO for record in caplog.records)
        assert "sensitive-" not in caplog.text
        owner, loop, kwargs = owners[0]
        assert owner is not threading.current_thread()
        assert kwargs["timeout"] == 0.04 and kwargs["max_attempts"] == 1
        assert client.loops == [loop]
    finally:
        release.set()
        assert runtime.close(timeout=1)
    runtime.start()
    assert runtime.close(timeout=1)
    assert len(owners) == 1 and client.close_calls == 1
    assert not owners[0][0].is_alive() and owners[0][1].is_closed()


@pytest.mark.parametrize("finish", ["shutdown", "factory_error"])
def test_late_initialization_never_reports_ready_after_shutdown_or_failure(caplog, finish):
    entered, release = threading.Event(), threading.Event()
    client = StartingClient()
    owners = []

    def factory(**_kwargs):
        owners.append(threading.current_thread())
        entered.set()
        assert release.wait(2)
        if finish == "factory_error":
            raise ValueError("sensitive-factory-diagnostic")
        return client

    runtime = HindsightRecallRuntime(
        base_url="http://127.0.0.1:8888", api_key=None, timeout_seconds=0.04, client_factory=factory
    )
    caplog.set_level(logging.INFO)
    try:
        runtime.start()
        assert entered.wait(0.5)
        assert recall(runtime) == ()
        assert "Optional Hindsight recall ready" not in caplog.messages
        if finish == "shutdown":
            assert not runtime.close(timeout=0.01)
        release.set()
        assert runtime._started.wait(1)
        assert recall(runtime) == ()
    finally:
        release.set()
        assert runtime.close(timeout=1)
    runtime.start()
    assert runtime.close(timeout=1)
    assert len(owners) == 1 and not owners[0].is_alive()
    assert client.calls == []
    assert client.close_calls == (1 if finish == "shutdown" else 0)
    assert "Optional Hindsight recall ready" not in caplog.messages
    assert "sensitive-" not in caplog.text


def test_readiness_logging_cannot_hold_recall_admission_lock(caplog):
    entered, release = threading.Event(), threading.Event()
    client = StartingClient()

    class ReadinessHandler(logging.Handler):
        def emit(self, _record):
            entered.set()
            release.wait(2)

    handler = ReadinessHandler()
    handler.addFilter(lambda record: record.getMessage() == "Optional Hindsight recall ready")
    logger = logging.getLogger()
    logger.addHandler(handler)
    caplog.set_level(logging.INFO)
    runtime = HindsightRecallRuntime(
        base_url="http://127.0.0.1:8888", api_key=None, timeout_seconds=0.04, client_factory=lambda **_: client
    )
    with ThreadPoolExecutor(max_workers=1) as caller:
        try:
            caller.submit(runtime.start).result(timeout=0.5)
            assert entered.wait(0.5)
            assert caller.submit(recall, runtime).result(timeout=0.3) == ()
            assert runtime._started.is_set()
        finally:
            release.set()
            assert runtime.close(timeout=1)
            logger.removeHandler(handler)
            handler.close()
    assert client.calls == [] and client.close_calls == 1
