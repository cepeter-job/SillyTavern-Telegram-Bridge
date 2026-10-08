"""Real worker threads must never inherit the previous session's trace."""

import logging
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import bridge.background as background
from bridge.diagnostic_events import diagnostic_context, diagnostic_scope, scope_reference
from bridge.scheduler_safety import DurableWorkerGuard


@pytest.fixture
def isolated_queue(monkeypatch):
    with ThreadPoolExecutor(max_workers=1) as executor:
        monkeypatch.setattr(background, "_UTILITY_EXECUTOR", executor)
        monkeypatch.setattr(background, "_UTILITY_SLOTS", threading.BoundedSemaphore(4))
        monkeypatch.setattr(background, "_BACKGROUND_ACCEPTING", True)
        monkeypatch.setattr(background, "_BACKGROUND_FUTURES", set())
        monkeypatch.setattr(background, "_BACKGROUND_FUTURE_LABELS", {})
        monkeypatch.setattr(background, "_CHAT_QUEUES", {})
        monkeypatch.setattr(background, "_CHAT_ACTIVE", set())
        monkeypatch.setattr(background, "_CHAT_IN_FLIGHT", set())
        monkeypatch.setattr(background, "_DURABLE_BACKLOG_DISPATCHER", None)
        yield executor


def test_ordered_queue_keeps_enqueue_time_identity_not_completion_callback_scope(isolated_queue, caplog):
    started = threading.Event()
    release = threading.Event()
    second_done = threading.Event()
    observed = []

    def first():
        started.set()
        assert release.wait(5)

    def second():
        observed.append(diagnostic_context())
        second_done.set()

    try:
        with caplog.at_level(logging.INFO):
            with diagnostic_scope(request_id="tg-first", chat_id="11", session_id="old"):
                assert background.submit_chat_background("test", "11", first)
            assert started.wait(5)
            with diagnostic_scope(request_id="tg-second", chat_id="11", session_id="new"):
                assert background.submit_chat_background("test", "11", second)
            release.set()
            assert second_done.wait(5)
            assert background.drain_background_jobs(5)
    finally:
        release.set()
    assert observed[0]["request_id"] == "tg-second"
    assert observed[0]["session_ref"] == scope_reference("session", "new")
    starts = [
        record.diagnostic_fields for record in caplog.records
        if hasattr(record, "diagnostic_fields") and record.diagnostic_fields["event"] == "background.start"
    ]
    assert [item["request_id"] for item in starts] == ["tg-first", "tg-second"]
    assert isolated_queue.submit(diagnostic_context).result(timeout=5) == {}


def test_parent_label_cannot_break_ordered_admission(isolated_queue):
    completed = threading.Event()
    try:
        with diagnostic_scope(label="parent", request_id="tg-label"):
            assert background.submit_chat_background("test", "11", completed.set)
        assert completed.wait(5)
        assert background.drain_background_jobs(5)
    finally:
        completed.set()


def test_recovered_identity_also_applies_to_outer_worker_events(isolated_queue, caplog):
    with sqlite3.connect(":memory:") as db:
        db.execute(
            "CREATE TABLE jobs(job_id INTEGER,update_id INTEGER,chat_id TEXT,session_id TEXT,kind TEXT,attempts INTEGER)"
        )
        db.execute("INSERT INTO jobs VALUES(8,12,'11','saved','generation',1)")
        worker = DurableWorkerGuard(lambda *args, **kwargs: None).prepare(db, 8, diagnostic_context)
        with caplog.at_level(logging.INFO), diagnostic_scope(request_id="foreign", chat_id="99", session_id="other"):
            future = background._submit_tracked_future("test", worker)
            result = future.result(timeout=5)
            assert background.drain_background_jobs(5)
    assert result["request_id"] == "tg-12"
    items = [
        record.diagnostic_fields for record in caplog.records
        if hasattr(record, "diagnostic_fields")
        and record.diagnostic_fields["event"] in {"background.start", "job.worker_start"}
    ]
    assert len(items) == 2
    assert {item["request_id"] for item in items} == {"tg-12"}
    assert {item["session_ref"] for item in items} == {scope_reference("session", "saved")}
