"""Retirement cleanup survives deletion of the last canonical session."""

import sqlite3
from types import SimpleNamespace

import pytest
from settings_test_support import make_test_settings
from test_story_memory_scope import forbid_real_hindsight_client as forbid_real_hindsight_client

from bridge import memory_backend
from bridge.memory_workers import dispatch_memory_backlog
from bridge.schema import initialize_database_schema


@pytest.mark.parametrize("failure", [False, True])
def test_deleted_session_cleanup_retries_without_ghost_transcript_jobs(tmp_path, monkeypatch, failure):
    path = tmp_path / "retirement.sqlite"
    db = sqlite3.connect(path)
    initialize_database_schema(db)
    db.execute(
        "INSERT INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,"
        "world_file,created_at,updated_at) VALUES('c','old','Story','','m','','',1,1)"
    )
    db.execute("INSERT INTO hindsight_documents VALUES('c','old','old-incarnation-document','native_fact',1)")
    db.execute("DELETE FROM sessions WHERE chat_id='c' AND session_id='old'")
    db.commit()
    submissions = []
    deleted = []

    class Client:
        def __init__(self):
            self.documents = self

        async def delete_document(self, **kwargs):
            deleted.append(kwargs["document_id"])
            if failure:
                raise RuntimeError("synthetic unavailable transport")

        def close(self):
            pass

    monkeypatch.setattr(memory_backend, "hindsight_client", lambda **kwargs: Client())
    services = SimpleNamespace(
        config=make_test_settings(home=tmp_path, db_file=path),
        db_factory=lambda: sqlite3.connect(path),
        background=SimpleNamespace(submit=lambda *args: submissions.append(args) or True),
    )
    try:
        assert db.execute("SELECT count(*) FROM memory_jobs").fetchone()[0] == 0
        assert dispatch_memory_backlog(services, db) == 1
        assert len(submissions) == 1
        assert dispatch_memory_backlog(services, db) == 0
        _, function, *args = submissions.pop()
        function(*args)
        assert deleted == ["old-incarnation-document"]
        assert db.execute("SELECT count(*) FROM memory_jobs").fetchone()[0] == 0
        done, attempts, next_attempt, token = db.execute(
            "SELECT deleted,attempts,next_attempt_at,lease_token FROM memory_retired_documents"
        ).fetchone()
        assert token == ""
        if failure:
            assert done == 0 and attempts > 0 and next_attempt > 0
            assert dispatch_memory_backlog(services, db) == 0
            failure = False
            db.execute("UPDATE memory_retired_documents SET next_attempt_at=0")
            db.commit()
            assert dispatch_memory_backlog(services, db) == 1
            _, function, *args = submissions.pop()
            function(*args)
        assert db.execute("SELECT deleted FROM memory_retired_documents").fetchone()[0] == 1
    finally:
        db.close()


def test_cleanup_never_deletes_recreated_current_source_even_when_memory_is_off(tmp_path, monkeypatch):
    from bridge.memory_store import next_source_segment, store_segment

    path = tmp_path / "recreated.sqlite"
    db = sqlite3.connect(path)
    initialize_database_schema(db)
    db.execute(
        "INSERT INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,"
        "world_file,created_at,updated_at) VALUES('c','s','New','','m','','',99,99)"
    )
    db.execute("INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('c','s','user','new',100)")
    db.commit()
    source = next_source_segment(db, "c", "s", "hindsight")
    store_segment(db, source)
    db.execute(
        "INSERT INTO memory_retired_documents(chat_id,session_id,document_id) VALUES('c','s',?)", (source.document_id,)
    )
    db.execute("UPDATE memory_jobs SET completed_version=dirty_version")
    db.execute("INSERT INTO meta(key,value) VALUES('memory_mode:c','off')")
    db.commit()
    submitted = []
    services = SimpleNamespace(
        config=make_test_settings(home=tmp_path, db_file=path),
        db_factory=lambda: sqlite3.connect(path),
        background=SimpleNamespace(submit=lambda *args: submitted.append(args) or True),
    )
    try:
        assert dispatch_memory_backlog(services, db) == 1
        _, function, *args = submitted.pop()
        function(*args)
        assert db.execute(
            "SELECT valid FROM memory_segments WHERE document_id=?", (source.document_id,)
        ).fetchone() == (1,)
        assert db.execute("SELECT next_attempt_at FROM memory_retired_documents").fetchone()[0] > 0
    finally:
        db.close()


def test_cleanup_renews_all_remaining_claimed_documents_before_each_call(monkeypatch):
    db = sqlite3.connect(":memory:")
    initialize_database_schema(db)
    db.executemany(
        "INSERT INTO memory_retired_documents(chat_id,session_id,document_id,lease_token,lease_deadline) "
        "VALUES('c','orphan',?,'worker',1001)",
        [("one",), ("two",)],
    )
    db.commit()
    clock = [1000.0]
    deleted = []

    class Client:
        def __init__(self):
            self.documents = self

        async def delete_document(self, **kwargs):
            deadlines = db.execute(
                "SELECT lease_deadline FROM memory_retired_documents WHERE deleted=0 AND lease_token='worker'"
            ).fetchall()
            assert deadlines and all(deadline >= clock[0] + 900 for (deadline,) in deadlines)
            deleted.append(kwargs["document_id"])
            clock[0] += 200

        def close(self):
            pass

    monkeypatch.setattr(memory_backend.time, "time", lambda: clock[0])
    monkeypatch.setattr(memory_backend, "hindsight_client", lambda **kwargs: Client())
    try:
        assert memory_backend.cleanup_retired_memory_documents(
            db, "c", "orphan", app_settings=make_test_settings(), lease_token="worker"
        )
        assert deleted == ["one", "two"]
    finally:
        db.close()
