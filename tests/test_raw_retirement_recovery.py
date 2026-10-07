"""Historical dispatch uncertainty survives crashes, late writes and cleanup owner races."""

import json
import os
import signal
import sqlite3
import time
import traceback
from types import SimpleNamespace

import pytest
from memory_runtime_test_support import isolated_memory_runtime as isolated_memory_runtime
from settings_test_support import make_test_settings
from test_memory_final_integration import (
    FIELDS,
    SESSION,
    Archive,
    append,
    capture_historical_raw,
    cleanup,
)
from test_memory_final_integration import legacy_runtime as legacy_runtime
from test_memory_final_integration import runtime as runtime

from bridge import memory_backend
from bridge.memory_fact_store import load_source, remember_local_fact
from bridge.memory_store import (
    fail_job,
    finish_archival_attempt,
    next_source_segment,
    reconcile_archival_attempts,
    reserve_archival_source,
    store_segment,
)
from bridge.memory_workers import dispatch_memory_backlog, run_memory_claim
from bridge.migrations import run_migrations
from bridge.schema import SCHEMA_MIGRATIONS, initialize_database_schema
from bridge.sqlite_store import db_connect


def dispatch_once(db, settings, *, startup=False):
    db.execute("UPDATE memory_jobs SET next_attempt_at=9999999999 WHERE layer<>'hindsight'")
    db.commit()
    submitted = []
    services = SimpleNamespace(
        config=settings,
        db_factory=lambda: db_connect(app_settings=settings),
        background=SimpleNamespace(submit=lambda *args: submitted.append(args) or True),
    )
    dispatch_memory_backlog(services, db, startup=startup)
    for _, function, *args in submitted:
        if function.__name__ == "_memory_worker":
            run_memory_claim(db, args[1], SESSION, FIELDS, app_settings=settings)
        else:
            function(*args)
    return submitted


@pytest.mark.parametrize("boundary", ["request", "reopening"])
@pytest.mark.skipif(not hasattr(os, "fork"), reason="Actual crash regression requires POSIX fork")
def test_historical_process_loss_recovers_without_fabricated_settlement(
    legacy_runtime, monkeypatch, tmp_path, boundary
):
    settings, db, archive = legacy_runtime
    append(db, "Delayed object survives process loss")
    source, token, payload = capture_historical_raw(db)
    initialize_database_schema(db)
    cleanup(db, settings)
    registry = tmp_path / "historical-remote-object.json"
    child_error = tmp_path / "child-error.txt"
    child = os.fork()
    if child == 0:
        try:
            child_db = db_connect(app_settings=settings)
            registry.write_text(json.dumps(payload), encoding="utf-8")
            if boundary == "request":
                os._exit(71)
            finish_archival_attempt(child_db, token)
            child_db.create_function("crash_process", 0, lambda: os._exit(71))
            child_db.execute(
                "CREATE TRIGGER crash_reopening BEFORE UPDATE ON memory_retired_documents "
                "BEGIN SELECT crash_process(); END"
            )
            child_db.commit()
            reconcile_archival_attempts(child_db)
            os._exit(72)
        except BaseException:
            child_error.write_text(traceback.format_exc(), encoding="utf-8")
            os._exit(73)
    deadline = time.monotonic() + 30
    while True:
        stopped, status = os.waitpid(child, os.WNOHANG)
        if stopped:
            break
        if time.monotonic() >= deadline:
            os.kill(child, signal.SIGKILL)
            os.waitpid(child, 0)
            pytest.fail("The isolated crash child did not finish within 30 seconds")
        time.sleep(0.02)
    assert os.waitstatus_to_exitcode(status) == 71, child_error.read_text() if child_error.exists() else status
    archive.remote[source.document_id] = json.loads(registry.read_text())
    later = memory_backend.time.time() + 1000
    monkeypatch.setattr(memory_backend.time, "time", lambda: later)
    reopened = db_connect(app_settings=settings)
    try:
        reopened.execute("DROP TRIGGER IF EXISTS crash_reopening")
        reopened.commit()
        dispatch_once(reopened, settings, startup=True)
        cleanup(reopened, settings)
        assert source.document_id not in archive.remote
        assert load_source(reopened, source.document_id) is None
        assert reopened.execute(
            "SELECT finished FROM memory_archival_attempts WHERE attempt_token=?", (token,)
        ).fetchone() == ((0,) if boundary == "request" else None)
        assert archive.retained == []
    finally:
        reopened.close()


def test_oldest_raw_request_survives_newer_same_id_completion_and_restart(legacy_runtime, monkeypatch):
    settings, db, archive = legacy_runtime
    append(db, "Same deterministic object, distinct historical requests")
    source, oldest, payload = capture_historical_raw(db)
    replacement, newer, _ = capture_historical_raw(db)
    assert replacement.document_id == source.document_id and newer != oldest
    initialize_database_schema(db)
    finish_archival_attempt(db, newer)
    reconcile_archival_attempts(db)
    cleanup(db, settings)
    assert db.execute("SELECT attempt_token,finished FROM memory_archival_attempts").fetchall() == [(oldest, 0)]
    archive.remote[source.document_id] = payload
    before = len(archive.deleted)
    assert dispatch_once(db, settings) == []
    assert len(archive.deleted) == before
    later = memory_backend.time.time() + 1000
    monkeypatch.setattr(memory_backend.time, "time", lambda: later)
    reopened = db_connect(app_settings=settings)
    try:
        dispatch_once(reopened, settings, startup=True)
        cleanup(reopened, settings)
        assert source.document_id not in archive.remote
        assert reopened.execute("SELECT attempt_token,finished FROM memory_archival_attempts").fetchall() == [
            (oldest, 0)
        ]
        assert archive.retained == []
    finally:
        reopened.close()


@pytest.mark.parametrize("replace_cleanup_owner", [False, True])
def test_delete_ack_cannot_erase_historical_completion_during_delete(
    legacy_runtime, monkeypatch, replace_cleanup_owner
):
    settings, db, archive = legacy_runtime
    append(db, "Historical retain and DELETE cross in flight")
    source, token, payload = capture_historical_raw(db)
    initialize_database_schema(db)
    db.execute("UPDATE memory_retired_documents SET lease_token='old-cleaner',lease_deadline=9999999999")
    db.commit()
    delete = archive.delete_document

    async def crossing_delete(**kwargs):
        assert not db.in_transaction
        await delete(**kwargs)
        archive.remote[source.document_id] = payload
        other = db_connect(app_settings=settings)
        try:
            finish_archival_attempt(other, token)
            reconcile_archival_attempts(other)
            if replace_cleanup_owner:
                other.execute("UPDATE memory_retired_documents SET lease_token='new-cleaner',lease_deadline=9999999999")
                other.commit()
        finally:
            other.close()

    monkeypatch.setattr(archive, "delete_document", crossing_delete)
    memory_backend.cleanup_retired_memory_documents(db, "c", "s", app_settings=settings, lease_token="old-cleaner")
    assert source.document_id in archive.remote
    owner = "new-cleaner" if replace_cleanup_owner else "old-cleaner"
    assert db.execute("SELECT deleted,lease_token FROM memory_retired_documents").fetchone() == (0, owner)
    monkeypatch.setattr(archive, "delete_document", delete)
    assert memory_backend.cleanup_retired_memory_documents(db, "c", "s", app_settings=settings, lease_token=owner)
    assert source.document_id not in archive.remote
    assert archive.retained == []


def test_recurring_watches_are_bounded_and_yield_to_normal_claims(runtime):
    settings, db, archive = runtime
    append(db, "Fresh canonical work must get a turn")
    remember_local_fact(db, "c", "s", "Bob", "Fresh native fact.")
    for number in range(20):
        document_id = f"orphan-raw-{number:02}"
        db.execute(
            "INSERT INTO memory_archival_attempts(attempt_token,document_id,chat_id,session_id) VALUES(?,?,?,?)",
            (f"unknown-{number}", document_id, "c", "orphan"),
        )
        db.execute(
            "INSERT INTO memory_retired_documents(chat_id,session_id,document_id) VALUES('c','orphan',?)",
            (document_id,),
        )
        archive.remote[document_id] = {}
    db.commit()
    submitted = []
    services = SimpleNamespace(
        config=settings,
        db_factory=lambda: db_connect(app_settings=settings),
        background=SimpleNamespace(submit=lambda *args: submitted.append(args) or True),
    )
    assert dispatch_memory_backlog(services, db) == 1
    _, function, *args = submitted.pop()
    function(*args)
    assert len(archive.deleted) == 16
    assert dispatch_memory_backlog(services, db) == 1
    _, function, *args = submitted.pop()
    assert function.__name__ == "_memory_worker"
    fail_job(db, args[1], deferred=True)
    assert dispatch_memory_backlog(services, db) == 1
    _, function, *args = submitted.pop()
    function(*args)
    assert len(archive.deleted) == 20
    assert db.execute("SELECT count(*) FROM memory_archival_attempts").fetchone() == (20,)
    assert db.execute(
        "SELECT count(*) FROM memory_retired_documents WHERE deleted=0 AND next_attempt_at>0"
    ).fetchone() == (20,)


def test_v23_upgrade_recovers_recorded_raw_ids_without_inventing_history(tmp_path, monkeypatch):
    settings = make_test_settings(home=tmp_path, db_file=tmp_path / "v23.sqlite")
    archive = Archive()
    monkeypatch.setattr(memory_backend, "hindsight_client", lambda **kwargs: archive)
    db = sqlite3.connect(settings.db_file)
    run_migrations(db, tuple(m for m in SCHEMA_MIGRATIONS if m.version <= 23))
    db.execute(
        "INSERT INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,"
        "world_file,created_at,updated_at) VALUES('c','s','Story','','m','','',1,1)"
    )
    append(db, "Recorded accepted identity")
    current = next_source_segment(db, "c", "s", "hindsight")
    store_segment(db, current)
    append(db, "Recorded pending identity")
    pending = next_source_segment(db, "c", "s", "hindsight")
    assert reserve_archival_source(db, pending)
    db.execute("INSERT INTO hindsight_documents VALUES('c','gone','mapped-only','source_segment',1)")
    db.execute(
        "INSERT INTO memory_segments SELECT 'old-invalid','c','gone',9,'hindsight',start_id,end_id,"
        "start_offset,end_offset,source_digest,rewrite_identity,purge_epoch,0,created_at "
        "FROM memory_segments WHERE document_id=?",
        (current.document_id,),
    )
    db.execute(
        "INSERT INTO memory_retired_documents(chat_id,session_id,document_id,deleted) "
        "VALUES('c','gone','old-invalid',1)"
    )
    db.commit()
    initialize_database_schema(db)
    before = db.execute(
        "SELECT attempt_token,session_created_at,started_at,finished FROM memory_archival_attempts"
    ).fetchall()
    initialize_database_schema(db)
    assert (
        db.execute(
            "SELECT attempt_token,session_created_at,started_at,finished FROM memory_archival_attempts"
        ).fetchall()
        == before
    )
    assert len(before) == 4
    assert db.execute(
        "SELECT session_created_at,started_at,finished FROM memory_archival_attempts WHERE document_id='mapped-only'"
    ).fetchone() == (None, None, 0)
    assert db.execute("SELECT covered_id FROM memory_layer_state WHERE layer='hindsight'").fetchone() == (0,)
    archive.remote.update({"old-invalid": {}, "mapped-only": {}, current.document_id: {}, pending.document_id: {}})
    dispatch_once(db, settings, startup=True)
    cleanup_result = memory_backend.cleanup_retired_memory_documents(db, "c", "gone", app_settings=settings)
    assert cleanup_result
    cleanup(db, settings)
    assert archive.remote == {}
    assert load_source(db, current.document_id) is None
    assert load_source(db, pending.document_id) is None
    db.close()


def test_queued_purge_cannot_discharge_historical_late_write(legacy_runtime, monkeypatch):
    from bridge.memory_retirement_store import queue_session_memory_cleanup
    from bridge.sqlite_store import write_transaction

    settings, db, archive = legacy_runtime
    append(db, "Raw completion crosses locally committed purge")
    source, token, payload = capture_historical_raw(db)
    initialize_database_schema(db)
    with write_transaction(db):
        queue_session_memory_cleanup(db, "c", "s")
    for _ in range(12):
        if not dispatch_once(db, settings):
            break
    archive.remote[source.document_id] = payload
    later = memory_backend.time.time() + 1000
    monkeypatch.setattr(memory_backend.time, "time", lambda: later)
    for _ in range(12):
        if not dispatch_once(db, settings, startup=True):
            break
    assert source.document_id not in archive.remote
    assert db.execute("SELECT finished FROM memory_archival_attempts WHERE attempt_token=?", (token,)).fetchone() == (
        0,
    )
    assert archive.retained == []
