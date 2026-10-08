"""Observe real lifecycle owners through their existing injected boundaries."""

import json
import logging
import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest

from bridge.diagnostic_events import diagnostic_context, diagnostic_scope


def events(caplog):
    return [record.diagnostic_fields for record in caplog.records if hasattr(record, "diagnostic_fields")]


def test_telegram_routing_uses_update_identity_and_restores_parent(monkeypatch, caplog):
    from bridge import update_routing

    caplog.set_level(logging.INFO, logger="bridge.events")
    seen = []
    monkeypatch.setattr(update_routing, "complete_update", lambda *_args: None)

    def route(*_args):
        seen.append(diagnostic_context())
        return True

    monkeypatch.setattr(update_routing, "route_message_update", route)
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("CREATE TABLE processed_updates(update_id INTEGER)")
        with diagnostic_scope(request_id="unrelated", session_id="private-other-story"):
            assert update_routing.route_update(SimpleNamespace(), db, {}, {"update_id": 42}, 0, frozenset()) == 43
            assert diagnostic_context()["request_id"] == "unrelated"
    assert seen[0]["request_id"] == "tg-42"
    assert "session_ref" not in seen[0]
    records = events(caplog)
    assert records[0]["event"] == "telegram.update_received"
    assert records[-1]["event"] == "telegram.update_finish"
    assert records[-1]["status"] == "succeeded"


def test_telegram_failure_does_not_log_update_text_or_exception_message(monkeypatch, caplog):
    from bridge import update_routing

    caplog.set_level(logging.INFO, logger="bridge.events")

    def fail(*_args):
        raise RuntimeError("PRIVATE_ERROR_TEXT")

    monkeypatch.setattr(update_routing, "route_message_update", fail)
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("CREATE TABLE processed_updates(update_id INTEGER)")
        with pytest.raises(RuntimeError):
            update_routing.route_update(
                SimpleNamespace(), db, {}, {"update_id": 7, "message": {"text": "PRIVATE_STORY"}}, 0, frozenset()
            )
    records = events(caplog)
    assert records[-1]["status"] == "failed"
    assert "PRIVATE" not in json.dumps(records)
    assert records[-1]["error_type"] == "RuntimeError"
    assert diagnostic_context() == {}


def test_job_events_report_backend_acceptance_without_payloads_or_errors(caplog):
    from bridge.job_service import JobService

    caplog.set_level(logging.INFO, logger="bridge.events")
    service = JobService(
        enqueue_backend=lambda *_args: 8,
        payload_backend=lambda *_args: True,
        actor_backend=lambda *_args: "12345",
        schedule_backend=lambda *_args: True,
        start_backend=lambda *_args: True,
        finish_backend=lambda _db, _id, state, _error: state == "failed",
        recover_backend=lambda *_args, **_kwargs: [],
        submit_chat=lambda *_args: True,
        delivery_retry_backend=lambda *_args: False,
    )
    with closing(sqlite3.connect(":memory:")) as db:
        assert service.enqueue(db, 42, "12345", "story-a", 1, "generation", {"text": "PRIVATE_STORY"}) == 8
        with diagnostic_scope(request_id="tg-42", job_id=8, chat_id="12345", session_id="story-a"):
            assert service.start(db, 8)
            assert not service.complete(db, 8)
            assert service.fail(db, 8, "PRIVATE_ERROR")
            assert not service.retry_delivery(db, 8, "PRIVATE_ERROR")
    records = events(caplog)
    assert next(item for item in records if item["event"] == "job.enqueued")["job_id"] == 8
    assert next(item for item in records if item["event"] == "job.started")["accepted"] is True
    assert next(item for item in records if item["event"] == "job.completed")["accepted"] is False
    assert next(item for item in records if item["event"] == "job.failed")["accepted"] is True
    assert next(item for item in records if item["event"] == "delivery.retry_requested")["accepted"] is False
    assert "PRIVATE" not in json.dumps(records)
