"""Exercise real retention boundaries rather than scanner source heuristics."""

from __future__ import annotations

import gc
import json
import threading
import tracemalloc
import weakref
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

import pytest

from bridge import background, native_cache


class Payload:
    def __init__(self):
        self.data = bytearray(64 * 1024)


@pytest.fixture
def isolated_background(monkeypatch):
    for name, value in {
        "_CHAT_QUEUES": {},
        "_CHAT_ACTIVE": set(),
        "_CHAT_IN_FLIGHT": set(),
        "_BACKGROUND_FUTURES": set(),
        "_BACKGROUND_FUTURE_LABELS": {},
        "_BACKGROUND_ACCEPTING": True,
        "_DURABLE_BACKLOG_DISPATCHER": None,
        "_GENERATION_SLOTS": threading.BoundedSemaphore(2),
    }.items():
        monkeypatch.setattr(background, name, value)


def test_shutdown_releases_unscheduled_payloads(isolated_background):
    slot = background._GENERATION_SLOTS
    slot.acquire()
    slot.acquire()
    payload = Payload()
    reference = weakref.ref(payload)
    try:
        assert background.submit_chat_background("generation", "chat", lambda value: None, payload)
        del payload
        assert reference() is not None
        background.begin_background_shutdown()
        assert reference() is None
        assert not background._CHAT_QUEUES
        assert not background._CHAT_ACTIVE
    finally:
        slot.release()
        slot.release()


def test_shutdown_during_executor_submission_does_not_restore_payload(isolated_background, monkeypatch):
    def reject(*args, **kwargs):
        background.begin_background_shutdown()
        return None

    monkeypatch.setattr(background, "_submit_tracked_future", reject)
    payload = Payload()
    reference = weakref.ref(payload)
    background.submit_chat_background("generation", "chat", lambda value: None, payload)
    del payload
    assert reference() is None
    assert not background._CHAT_QUEUES
    assert not background._CHAT_IN_FLIGHT
    assert not background._CHAT_ACTIVE


def test_shutdown_discards_waiters_without_interrupting_running_job(isolated_background, monkeypatch):
    started, release, completed = threading.Event(), threading.Event(), threading.Event()

    def running():
        started.set()
        assert release.wait(3)
        completed.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        monkeypatch.setattr(background, "_executor_for", lambda label: executor)
        try:
            assert background.submit_chat_background("generation", "chat", running)
            assert started.wait(3)
            payload = Payload()
            reference = weakref.ref(payload)
            assert background.submit_chat_background("generation", "chat", lambda value: None, payload)
            del payload
            background.begin_background_shutdown()
            assert reference() is None
            assert background._CHAT_IN_FLIGHT == {"chat"}
        finally:
            release.set()
    assert completed.is_set()
    assert not background._CHAT_IN_FLIGHT
    assert not background._BACKGROUND_FUTURES
    assert not background._BACKGROUND_FUTURE_LABELS


@pytest.fixture
def isolated_native_cache(monkeypatch):
    monkeypatch.setattr(native_cache, "_NATIVE_CACHE", OrderedDict())


def test_compact_json_cannot_exceed_retained_heap_budget(isolated_native_cache, monkeypatch, tmp_path):
    monkeypatch.setattr(native_cache, "_NATIVE_CACHE_MAX_HEAP_BYTES", 1024, raising=False)
    path = tmp_path / "compact.json"
    value = [{"x": index} for index in range(100)]
    path.write_text(json.dumps(value), encoding="utf-8")
    assert native_cache.cached_json(path) == value
    assert not native_cache._NATIVE_CACHE


def test_metadata_expansion_cannot_bypass_heap_budget(isolated_native_cache, monkeypatch, tmp_path):
    monkeypatch.setattr(native_cache, "_NATIVE_CACHE_MAX_HEAP_BYTES", 1024, raising=False)
    path = tmp_path / "compressed.png"
    path.write_bytes(b"small compressed source")
    value = {"description": "x" * 8192}
    assert native_cache.cached_png_metadata(path, lambda current: value) == value
    assert not native_cache._NATIVE_CACHE


def test_uncached_native_result_is_not_unnecessarily_deep_copied(isolated_native_cache, monkeypatch, tmp_path):
    monkeypatch.setattr(native_cache, "_NATIVE_CACHE_MAX_SOURCE_BYTES", 1)
    path = tmp_path / "large.png"
    path.write_bytes(b"uncacheable source")
    value = {"entries": [{"key": index} for index in range(100)]}
    assert native_cache.cached_png_metadata(path, lambda current: value) is value


def test_native_cache_total_heap_budget_evicts_lru(isolated_native_cache, monkeypatch, tmp_path):
    monkeypatch.setattr(native_cache, "_NATIVE_CACHE_MAX_HEAP_BYTES", 2500, raising=False)
    loaded = []

    def loader(path):
        loaded.append(path.name)
        return {"description": path.name * 1200}

    for name in ("a", "b", "a"):
        path = tmp_path / name
        if not path.exists():
            path.write_bytes(b"source")
        assert native_cache.cached_png_metadata(path, loader)["description"] == name * 1200
    assert loaded == ["a", "b", "a"]
    assert len(native_cache._NATIVE_CACHE) == 1


def test_native_heap_accounting_handles_cycles_and_shared_children(isolated_native_cache, monkeypatch, tmp_path):
    monkeypatch.setattr(native_cache, "_NATIVE_CACHE_MAX_HEAP_BYTES", 4096)
    path = tmp_path / "shared.png"
    path.write_bytes(b"source")
    shared = "x" * 2000
    value = {"items": [shared] * 10}
    value["self"] = value
    result = native_cache.cached_png_metadata(path, lambda current: value)
    assert result is not value
    assert result["self"] is result
    assert result["items"] == [shared] * 10
    assert len(native_cache._NATIVE_CACHE) == 1


def test_unknown_native_object_graph_is_not_retained(isolated_native_cache, tmp_path):
    path = tmp_path / "custom.png"
    path.write_bytes(b"source")
    value = {"opaque": Payload()}
    assert native_cache.cached_png_metadata(path, lambda current: value) is value
    assert not native_cache._NATIVE_CACHE


def test_shutdown_rejects_new_ordered_jobs(isolated_background):
    background.begin_background_shutdown()
    assert not background.submit_chat_background("generation", "chat", lambda: None)
    assert not background._CHAT_QUEUES
    assert not background._CHAT_ACTIVE


def test_high_churn_shutdown_releases_payload_heap(isolated_background):
    """Queued work must release both object reachability and traced heap on shutdown."""
    slot = background._GENERATION_SLOTS
    slot.acquire()
    slot.acquire()
    owns_tracing = not tracemalloc.is_tracing()
    if owns_tracing:
        tracemalloc.start(1)
    try:
        gc.collect()
        baseline = tracemalloc.get_traced_memory()[0]
        references = []
        for index in range(128):
            payload = Payload()
            references.append(weakref.ref(payload))
            assert background.submit_chat_background(
                "generation",
                f"memory-leak-churn:{index}",
                lambda value: None,
                payload,
            )
        del payload

        queued = tracemalloc.get_traced_memory()[0] - baseline
        assert queued >= 6 * 1024 * 1024

        background.begin_background_shutdown()
        gc.collect()
        retained = tracemalloc.get_traced_memory()[0] - baseline

        assert all(reference() is None for reference in references)
        assert retained < 1024 * 1024
        assert not background._CHAT_QUEUES
        assert not background._CHAT_ACTIVE
        assert not background._CHAT_IN_FLIGHT
        assert not background._BACKGROUND_FUTURES
        assert not background._BACKGROUND_FUTURE_LABELS
    finally:
        if owns_tracing:
            tracemalloc.stop()
        slot.release()
        slot.release()
