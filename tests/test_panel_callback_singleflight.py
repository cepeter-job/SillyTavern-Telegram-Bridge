from __future__ import annotations

import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest

from bridge.job_store import finish_job
from bridge.schema import initialize_database_schema


@pytest.fixture
def callback_db():
    """Close test-owned SQLite handles even when callback assertions fail."""
    with closing(sqlite3.connect(":memory:")) as connection:
        yield connection


def _callback(data="characteroptimizeauto:token", *, sender="100", message_id=77):
    return {
        "id": f"cb-{data}-{sender}",
        "from": {"id": sender},
        "message": {
            "chat": {"id": "chat"},
            "message_id": message_id,
            "reply_markup": {"inline_keyboard": [[{"text": "Run", "callback_data": data}]]},
        },
        "data": data,
    }


def _db():
    db = sqlite3.connect(":memory:")
    initialize_database_schema(db)
    return db


def test_atomic_callback_admission_rejects_same_panel_until_terminal_state():
    from bridge.job_store import enqueue_panel_callback_job

    db = _db()
    try:
        first = enqueue_panel_callback_job(db, 1, "chat", "s", 77, {"callback": _callback(), "actor_id": "100"})
        assert isinstance(first, int)
        assert enqueue_panel_callback_job(db, 2, "chat", "s", 77, {"callback": _callback(), "actor_id": "100"}) is None

        finish_job(db, first, "done")
        second = enqueue_panel_callback_job(db, 3, "chat", "s", 77, {"callback": _callback(), "actor_id": "100"})
        assert isinstance(second, int)
        finish_job(db, second, "failed", "synthetic")
        assert isinstance(
            enqueue_panel_callback_job(db, 4, "chat", "s", 77, {"callback": _callback(), "actor_id": "100"}), int
        )
    finally:
        db.close()


def test_atomic_callback_admission_allows_different_panel_while_first_is_active():
    from bridge.job_store import enqueue_panel_callback_job

    db = _db()
    try:
        assert isinstance(
            enqueue_panel_callback_job(db, 10, "chat", "s", 77, {"callback": _callback(message_id=77)}), int
        )
        assert isinstance(
            enqueue_panel_callback_job(db, 11, "chat", "s", 78, {"callback": _callback(message_id=78)}), int
        )
    finally:
        db.close()


class _Jobs:
    def __init__(self, result=41):
        self.result = result
        self.events = []

    def enqueue_callback(self, *args):
        self.events.append(("enqueue", args))
        return self.result

    def submit(self, _db, job_id, _submission):
        self.events.append(("submit", job_id))
        return True


class _Session:
    def ensure(self, *_args):
        return {"session_id": "s"}


def _services(jobs, requests):
    return SimpleNamespace(
        config=SimpleNamespace(bot_token="token", default_model="model"),
        session=_Session(),
        jobs=jobs,
        telegram=SimpleNamespace(request=lambda token, method, payload: requests.append((method, payload)) or {}),
    )


def test_callback_ingress_marks_owned_panel_busy_before_submit(monkeypatch, callback_db):
    import bridge.update_callback_routing as routing

    db = callback_db
    jobs, requests, answers = _Jobs(), [], []
    monkeypatch.setattr(routing, "route_light_novel_callback", lambda *a, **k: False)
    monkeypatch.setattr(routing, "is_help_callback", lambda _data: False)
    monkeypatch.setattr(routing, "panel_owner_for_message", lambda *a: "100")
    monkeypatch.setattr(routing, "panel_session_for_message", lambda *a: "s")
    import bridge.panel_singleflight as singleflight

    monkeypatch.setattr(singleflight, "panel_binding_revision", lambda *a: 123.0)
    monkeypatch.setattr(routing, "answer_callback", lambda _t, _id, text="": answers.append(text))

    routing.route_callback_update(_services(jobs, requests), db, _callback(), 100, frozenset({"100"}))

    assert [event[0] for event in jobs.events] == ["enqueue", "submit"]
    assert requests == [
        (
            "editMessageReplyMarkup",
            {
                "chat_id": "chat",
                "message_id": 77,
                "reply_markup": {"inline_keyboard": [[{"text": "⏳ Processing…", "callback_data": "panelbusy"}]]},
            },
        )
    ]
    assert answers == ["Processing…"]
    stored_callback = jobs.events[0][1][-1]["callback"]
    assert stored_callback["_panel_busy_revision"] == 123.0
    assert stored_callback["_panel_original_reply_markup"] == {
        "inline_keyboard": [[{"text": "Run", "callback_data": "characteroptimizeauto:token"}]]
    }


def test_callback_ingress_rejects_second_click_and_foreign_owner_before_enqueue(monkeypatch, callback_db):
    import bridge.update_callback_routing as routing

    db = callback_db
    answers = []
    monkeypatch.setattr(routing, "route_light_novel_callback", lambda *a, **k: False)
    monkeypatch.setattr(routing, "is_help_callback", lambda _data: False)
    monkeypatch.setattr(routing, "answer_callback", lambda _t, _id, text="": answers.append(text))
    monkeypatch.setattr(routing, "panel_session_for_message", lambda *a: "s")
    import bridge.panel_singleflight as singleflight

    monkeypatch.setattr(singleflight, "panel_binding_revision", lambda *a: 123.0)

    foreign_jobs = _Jobs()
    monkeypatch.setattr(routing, "panel_owner_for_message", lambda *a: "100")
    routing.route_callback_update(_services(foreign_jobs, []), db, _callback(sender="200"), 101, frozenset({"200"}))
    assert foreign_jobs.events == []
    assert answers[-1] == "This panel belongs to another user"

    duplicate_jobs = _Jobs(result=None)
    routing.route_callback_update(_services(duplicate_jobs, []), db, _callback(sender="100"), 102, frozenset({"100"}))
    assert [event[0] for event in duplicate_jobs.events] == ["enqueue"]
    assert answers[-1] == "Already processing…"


def test_busy_button_is_answered_without_enqueuing(monkeypatch, callback_db):
    import bridge.update_callback_routing as routing

    db = callback_db
    jobs, answers = _Jobs(), []
    monkeypatch.setattr(routing, "route_light_novel_callback", lambda *a, **k: False)
    monkeypatch.setattr(routing, "answer_callback", lambda _t, _id, text="": answers.append(text))
    routing.route_callback_update(_services(jobs, []), db, _callback("panelbusy"), 103, frozenset({"100"}))
    assert jobs.events == []
    assert answers == ["Already processing…"]


def test_send_panel_request_treats_not_modified_as_idempotent(monkeypatch):
    from settings_test_support import make_test_settings

    import bridge.telegram as telegram
    from bridge.request_types import RequestContext

    db = _db()
    try:
        ctx = RequestContext(db, "s", "100", app_settings=make_test_settings())
        monkeypatch.setattr(
            telegram,
            "telegram_request",
            lambda *_a, **_k: (_ for _ in ()).throw(
                RuntimeError("Telegram editMessageText failed: Bad Request: message is not modified")
            ),
        )
        result = telegram.send_panel_request(
            "token",
            "editMessageText",
            {"chat_id": "chat", "message_id": 77, "text": "same", "reply_markup": {"inline_keyboard": []}},
            request_context=ctx,
        )
        assert result == {}
    finally:
        db.close()


def test_busy_panel_restore_reinstates_original_keyboard_when_handler_did_not_rerender(monkeypatch, callback_db):
    import bridge.panel_singleflight as singleflight

    db = callback_db
    callback = _callback()
    callback["_panel_busy_revision"] = 123.0
    callback["_panel_original_reply_markup"] = callback["message"]["reply_markup"]
    requests = []
    monkeypatch.setattr(singleflight, "panel_binding_revision", lambda *a: 123.0)

    assert singleflight.restore_busy_panel_if_unchanged(
        lambda _token, method, payload: requests.append((method, payload)) or {},
        "token",
        db,
        "chat",
        callback,
    )
    assert requests == [
        (
            "editMessageReplyMarkup",
            {
                "chat_id": "chat",
                "message_id": 77,
                "reply_markup": {
                    "inline_keyboard": [[{"text": "Run", "callback_data": "characteroptimizeauto:token"}]]
                },
            },
        )
    ]


def test_busy_panel_restore_does_not_overwrite_newer_panel_render(monkeypatch, callback_db):
    import bridge.panel_singleflight as singleflight

    db = callback_db
    callback = _callback()
    callback["_panel_busy_revision"] = 123.0
    callback["_panel_original_reply_markup"] = callback["message"]["reply_markup"]
    requests = []
    monkeypatch.setattr(singleflight, "panel_binding_revision", lambda *a: 456.0)

    assert not singleflight.restore_busy_panel_if_unchanged(
        lambda _token, method, payload: requests.append((method, payload)) or {},
        "token",
        db,
        "chat",
        callback,
    )
    assert requests == []


def test_recovered_running_callback_remains_singleflight_locked():
    from bridge.job_store import enqueue_panel_callback_job, recover_jobs

    db = _db()
    try:
        first = enqueue_panel_callback_job(db, 20, "chat", "s", 77, {"callback": _callback(), "actor_id": "100"})
        assert isinstance(first, int)
        db.execute("UPDATE jobs SET state='running' WHERE job_id=?", (first,))
        db.commit()

        recovered = recover_jobs(db, recover_running=True)
        assert recovered and recovered[0][0] == first
        assert db.execute("SELECT state FROM jobs WHERE job_id=?", (first,)).fetchone() == ("queued",)
        assert enqueue_panel_callback_job(db, 21, "chat", "s", 77, {"callback": _callback(), "actor_id": "100"}) is None
    finally:
        db.close()


def test_callback_worker_always_attempts_busy_restore(monkeypatch):
    import bridge.worker_orchestration as workers

    restored = []
    monkeypatch.setattr(workers, "restore_busy_panel_if_unchanged", lambda *a, **k: restored.append(True) or True)
    monkeypatch.setattr(workers, "operation_was_applied", lambda *a, **k: False)
    monkeypatch.setattr(workers, "resume_committed_callback", lambda *a, **k: False)
    monkeypatch.setattr(workers, "process_callback", lambda *a, **k: None)

    class Jobs:
        def start(self, _db, _job_id):
            return True

        def actor_id(self, _db, _job_id):
            return "100"

        def complete(self, _db, _job_id):
            return True

        def fail(self, _db, _job_id, _exc):
            return True

    db = _db()
    services = SimpleNamespace(
        config=SimpleNamespace(bot_token="token"),
        jobs=Jobs(),
        db_factory=lambda: db,
        telegram=SimpleNamespace(request=lambda *_a, **_k: {}, send_text=lambda *_a, **_k: []),
    )
    workers.process_callback_job(services, "chat", _callback(), 31)
    assert restored == [True]
