"""Callback notifications cannot replay an already decided update."""

from __future__ import annotations

import io
import sqlite3
import urllib.error
from contextlib import closing
from types import SimpleNamespace

import pytest

import bridge.telegram as telegram
import bridge.update_callback_routing as callbacks
from bridge.job_service import JobService
from bridge.job_store import (
    enqueue_job,
    enqueue_panel_callback_job,
    finish_job,
    job_actor_id,
    mark_job_running,
    mark_job_scheduled,
    recover_jobs,
    store_job_payload,
)
from bridge.schema import initialize_database_schema
from bridge.update_routing import route_update


def _update(update_id, *, message_id=77, data="character:fixture", sender="100"):
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"callback-{update_id}",
            "from": {"id": sender},
            "message": {"chat": {"id": "chat"}, "message_id": message_id},
            "data": data,
        },
    }


@pytest.fixture
def callback_runtime(monkeypatch):
    monkeypatch.setattr(callbacks, "route_light_novel_callback", lambda *a, **k: False)
    monkeypatch.setattr(callbacks, "panel_owner_for_message", lambda *a: "100")
    monkeypatch.setattr(callbacks, "panel_session_for_message", lambda *a: "session")
    jobs = JobService(
        enqueue_backend=enqueue_job,
        payload_backend=store_job_payload,
        actor_backend=job_actor_id,
        schedule_backend=mark_job_scheduled,
        start_backend=mark_job_running,
        finish_backend=finish_job,
        recover_backend=recover_jobs,
        submit_chat=lambda *a: True,
        callback_enqueue_backend=enqueue_panel_callback_job,
    )
    services = SimpleNamespace(
        config=SimpleNamespace(bot_token="fixture-token", default_model="model"),
        session=SimpleNamespace(ensure=lambda *a: {"session_id": "session"}),
        delivery=object(),
        jobs=jobs,
        telegram=SimpleNamespace(request=lambda *a: {}),
    )
    with closing(sqlite3.connect(":memory:")) as db:
        initialize_database_schema(db)
        yield db, services


def _fail_ack(monkeypatch, failure):
    def fail(_request, **_kwargs):
        if failure == "timeout":
            raise TimeoutError("synthetic timeout")
        raise urllib.error.HTTPError(
            "https://api.telegram.org/fixture",
            400,
            "Bad Request",
            {},
            io.BytesIO(b'{"ok":false,"description":"synthetic callback rejection"}'),
        )

    monkeypatch.setattr(telegram.urllib.request, "urlopen", fail)


@pytest.mark.parametrize("failure", ["http_error", "timeout"])
def test_admitted_callback_ack_failure_completes_without_duplicate_job(monkeypatch, callback_runtime, failure):
    db, services = callback_runtime
    _fail_ack(monkeypatch, failure)
    update = _update(101)

    assert route_update(services, db, {}, update, 0, frozenset({"100"})) == 102
    assert db.execute("SELECT value FROM meta WHERE key='telegram_offset'").fetchone() == ("102",)
    assert db.execute("SELECT update_id,state FROM jobs").fetchall() == [(101, "scheduled")]

    assert route_update(services, db, {}, update, 102, frozenset({"100"})) == 102
    assert db.execute("SELECT count(*) FROM jobs").fetchone() == (1,)


@pytest.mark.parametrize("failure", ["http_error", "timeout"])
def test_busy_rejection_ack_failure_retires_update_and_allows_next_update(monkeypatch, callback_runtime, failure):
    db, services = callback_runtime
    enqueue_panel_callback_job(db, 100, "chat", "session", 77, {"callback": {}})
    _fail_ack(monkeypatch, failure)
    rejected, following = _update(101), _update(102, message_id=78)
    offset = 101

    for update in (rejected, following):
        offset = route_update(services, db, {}, update, offset, frozenset({"100"}))

    assert offset == 103
    assert db.execute("SELECT value FROM meta WHERE key='telegram_offset'").fetchone() == ("103",)
    assert db.execute("SELECT update_id FROM processed_updates ORDER BY update_id").fetchall() == [
        (100,),
        (101,),
        (102,),
    ]
    assert db.execute("SELECT update_id FROM jobs ORDER BY update_id").fetchall() == [(100,), (102,)]
    for update in (rejected, following):
        assert route_update(services, db, {}, update, offset, frozenset({"100"})) == 103
    assert db.execute("SELECT count(*) FROM jobs").fetchone() == (2,)


@pytest.mark.parametrize("data,sender", [("panelbusy", "100"), ("character:fixture", "200")])
def test_terminal_callback_rejection_completes_when_ack_fails(monkeypatch, callback_runtime, data, sender):
    db, services = callback_runtime
    _fail_ack(monkeypatch, "http_error")

    assert route_update(services, db, {}, _update(101, data=data, sender=sender), 0, frozenset({sender})) == 102
    assert db.execute("SELECT update_id FROM processed_updates").fetchall() == [(101,)]
    assert db.execute("SELECT count(*) FROM jobs").fetchone() == (0,)


def test_unrelated_callback_failure_still_prevents_completion(monkeypatch, callback_runtime):
    db, services = callback_runtime

    def fail(*_args, **_kwargs):
        raise RuntimeError("application routing failed")

    monkeypatch.setattr(callbacks, "route_light_novel_callback", fail)
    with pytest.raises(RuntimeError, match="application routing failed"):
        route_update(services, db, {}, _update(101), 0, frozenset({"100"}))
    assert db.execute("SELECT count(*) FROM processed_updates").fetchone() == (0,)
    assert db.execute("SELECT value FROM meta WHERE key='telegram_offset'").fetchone() is None


@pytest.mark.parametrize("data", ["panelbusy", "help:cmdpage:missing:0"])
def test_unexpected_ack_programming_error_is_not_swallowed(monkeypatch, callback_runtime, data):
    db, services = callback_runtime

    def fail(*_args, **_kwargs):
        raise RuntimeError("unexpected application bug")

    monkeypatch.setattr(callbacks, "answer_callback", fail)
    with pytest.raises(RuntimeError, match="unexpected application bug"):
        route_update(services, db, {}, _update(101, data=data), 0, frozenset({"100"}))
    assert db.execute("SELECT count(*) FROM processed_updates").fetchone() == (0,)
    assert db.execute("SELECT value FROM meta WHERE key='telegram_offset'").fetchone() is None


@pytest.mark.parametrize("failure", ["http_error", "timeout"])
def test_expired_help_ack_failure_completes_update(monkeypatch, callback_runtime, failure):
    db, services = callback_runtime
    _fail_ack(monkeypatch, failure)

    assert route_update(services, db, {}, _update(101, data="help:cmdpage:missing:0"), 0, frozenset({"100"})) == 102
    assert db.execute("SELECT value FROM meta WHERE key='telegram_offset'").fetchone() == ("102",)
    assert db.execute("SELECT update_id FROM processed_updates").fetchall() == [(101,)]
    assert db.execute("SELECT count(*) FROM jobs").fetchone() == (0,)


def test_help_render_failure_still_prevents_completion_after_failed_ack(monkeypatch, callback_runtime):
    db, services = callback_runtime
    _fail_ack(monkeypatch, "timeout")

    def fail(*_args, **_kwargs):
        raise RuntimeError("application rendering failed")

    services.delivery = SimpleNamespace(send_panel_request=fail)
    with pytest.raises(RuntimeError, match="application rendering failed"):
        route_update(services, db, {}, _update(101, data="help:menu"), 0, frozenset({"100"}))
    assert db.execute("SELECT count(*) FROM processed_updates").fetchone() == (0,)
    assert db.execute("SELECT value FROM meta WHERE key='telegram_offset'").fetchone() is None
