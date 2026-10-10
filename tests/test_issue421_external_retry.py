"""Backoff must reduce repeated work without forgetting ambiguous late writes."""

import sqlite3

import pytest
from settings_test_support import make_test_settings
from test_durable_memory_workers import add
from test_memory_completion_safety import session_db as session_db

from bridge import memory_backend
from bridge.memory_store import claim_jobs, fail_job
from bridge.schema import initialize_database_schema


@pytest.mark.parametrize("finished", [False, True])
def test_old_retirement_watch_backs_off_but_never_resolves_ambiguity(tmp_path, monkeypatch, finished):
    db = sqlite3.connect(":memory:")
    initialize_database_schema(db)
    db.execute(
        "INSERT INTO memory_retired_documents(chat_id,session_id,document_id,attempts,lease_token) "
        "VALUES('owner','old','old-doc',100,'worker')"
    )
    db.execute(
        "INSERT INTO memory_archival_attempts(attempt_token,document_id,chat_id,session_id,"
        "session_created_at,started_at,kind,finished) VALUES('attempt','old-doc','owner','old',1,1,'raw',?)",
        (int(finished),),
    )
    db.commit()
    monkeypatch.setattr(memory_backend.time, "time", lambda: 1000.0)
    deleted = []

    class Client:
        def __init__(self):
            self.documents = self

        async def delete_document(self, **kwargs):
            deleted.append(kwargs["document_id"])

        def close(self):
            pass

    monkeypatch.setattr(memory_backend, "hindsight_client", lambda **kwargs: Client())
    try:
        memory_backend.cleanup_retired_memory_documents(
            db, "owner", "old", app_settings=make_test_settings(home=tmp_path), lease_token="worker"
        )
        assert deleted == ["old-doc"]
        terminal, retry_at = db.execute("SELECT deleted,next_attempt_at FROM memory_retired_documents").fetchone()
        if finished:
            assert terminal == 1
            assert db.execute("SELECT COUNT(*) FROM memory_archival_attempts").fetchone() == (0,)
        else:
            assert terminal == 0
            assert 3600 <= retry_at - 1000 <= 3600
            assert db.execute("SELECT finished FROM memory_archival_attempts").fetchone() == (0,)
    finally:
        db.close()


def test_repeated_external_retain_failure_uses_bounded_longer_backoff(session_db):
    _, db, _ = session_db
    add(db)
    db.execute("UPDATE memory_jobs SET attempts=20 WHERE layer='hindsight'")
    db.commit()
    claim = claim_jobs(db, layers=("hindsight",))[0]
    assert fail_job(db, claim, "retain_failed", now=1000.0)
    row = db.execute("SELECT completed_version,next_attempt_at FROM memory_jobs WHERE layer='hindsight'").fetchone()
    assert row == (0, 4600.0)


def test_retention_timeout_does_not_shorten_server_work_or_slow_recall(tmp_path, monkeypatch):
    calls = []

    class Client:
        def retain(self, **kwargs):
            pass

        def close(self):
            pass

    def factory(**kwargs):
        calls.append(kwargs.get("request_timeout", 30.0))
        return Client()

    monkeypatch.setattr(memory_backend, "hindsight_client", factory)
    settings = make_test_settings(home=tmp_path)
    with memory_backend.hindsight_client_scope(app_settings=settings):
        pass
    assert memory_backend._retain_with_client(
        "owner", "story", "doc", "Alice", "An accepted fact.", "synthetic", "native_fact", app_settings=settings
    )
    assert calls == [30.0, 90.0]
