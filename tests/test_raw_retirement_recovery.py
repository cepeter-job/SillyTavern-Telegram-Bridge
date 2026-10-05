"""Raw-retirement recovery at real client, SQLite, restart and dispatcher boundaries."""

import json
import os
import signal
import sqlite3
import threading
import time
import traceback
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from memory_runtime_test_support import isolated_memory_runtime as isolated_memory_runtime
from settings_test_support import make_test_settings
from test_memory_final_integration import (
    FIELDS,
    SESSION,
    Archive,
    append,
    change_source,
    cleanup,
    run,
    seed_or_worker,
)
from test_memory_final_integration import runtime as runtime

from bridge import memory, memory_backend
from bridge.memory_fact_store import load_source
from bridge.memory_store import claim_jobs, fail_job, next_source_segment, reserve_archival_source, store_segment
from bridge.memory_workers import dispatch_memory_backlog, run_memory_claim
from bridge.migrations import run_migrations
from bridge.schema import SCHEMA_MIGRATIONS, initialize_database_schema
from bridge.sqlite_store import db_connect


def dispatch_once(db, settings, *, startup=False):
    # This fixture supplies raw archival work, not unrelated extraction providers.
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
        if function.__name__ == "_retired_memory_worker":
            function(*args)
        else:
            run_memory_claim(db, args[1], SESSION, FIELDS, app_settings=settings)
    return submitted


@pytest.mark.parametrize("route", ["worker", "seed"])
@pytest.mark.parametrize("boundary", ["client", "reopening"])
@pytest.mark.skipif(not hasattr(os, "fork"), reason="Actual crash regression requires POSIX fork")
def test_raw_process_loss_recovers_without_settlement(runtime, monkeypatch, tmp_path, route, boundary):
    settings, db, archive = runtime
    row = append(db, "Delayed object survives process loss")
    registry = tmp_path / "remote-object.json"
    child_error = tmp_path / "child-error.txt"
    child = os.fork()
    if child == 0:
        try:
            # The pre-session I/O guards are inherited; replace only the consumed client.
            remote = Archive()
            monkeypatch.setattr(memory_backend, "hindsight_client", lambda **kwargs: remote)
            child_db = db_connect(app_settings=settings)

            def late(kwargs):
                change_source(child_db, row, "rewrite")
                memory_backend.cleanup_retired_memory_documents(child_db, "c", "s", app_settings=settings)
                registry.write_text(json.dumps(kwargs), encoding="utf-8")
                if boundary == "client":
                    os._exit(71)
                child_db.create_function("crash_process", 0, lambda: os._exit(71))
                child_db.execute(
                    "CREATE TRIGGER crash_reopening BEFORE UPDATE ON memory_retired_documents "
                    "BEGIN SELECT crash_process(); END"
                )
                child_db.commit()

            remote.on_retain = late
            seed_or_worker(route, child_db, settings)
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
    payload = json.loads(registry.read_text(encoding="utf-8"))
    old_id = payload["document_id"]
    archive.remote[old_id] = payload
    delete = archive.delete_document

    async def delete_persisted(**kwargs):
        await delete(**kwargs)
        if kwargs["document_id"] == old_id:
            registry.unlink(missing_ok=True)

    monkeypatch.setattr(archive, "delete_document", delete_persisted)
    later = memory_backend.time.time() + 1000
    monkeypatch.setattr(memory_backend.time, "time", lambda: later)
    reopened = db_connect(app_settings=settings)
    try:
        reopened.execute("DROP TRIGGER IF EXISTS crash_reopening")
        reopened.commit()
        dispatch_once(reopened, settings, startup=True)
        cleanup(reopened, settings)
        assert old_id not in archive.remote
        assert not registry.exists()
        assert load_source(reopened, old_id) is None
        assert reopened.execute("SELECT 1 FROM hindsight_documents WHERE document_id=?", (old_id,)).fetchone() is None
    finally:
        reopened.close()


@pytest.mark.parametrize("route", ["worker", "seed"])
def test_uncertain_request_can_write_after_failure_cleanup_and_restart(runtime, monkeypatch, route):
    settings, db, archive = runtime
    row = append(db, "Unknown upstream request")
    delayed = []

    def uncertain(kwargs):
        delayed.append(kwargs)
        raise RuntimeError("Transport failed while upstream still has the request")

    archive.on_retain = uncertain
    assert seed_or_worker(route, db, settings) == ("degraded" if route == "seed" else "retain_failed")
    old_id = delayed[0]["document_id"]
    change_source(db, row, "rewrite")
    archive.on_retain = None
    cleanup(db, settings)
    # Upstream finishes after the caller failed and cleanup already succeeded.
    archive.remote[old_id] = delayed[0]
    later = memory_backend.time.time() + 1000
    monkeypatch.setattr(memory_backend.time, "time", lambda: later)
    reopened = db_connect(app_settings=settings)
    try:
        dispatch_once(reopened, settings, startup=True)
        if next_source_segment(reopened, "c", "s", "hindsight") is not None:
            assert seed_or_worker(route, reopened, settings) == ("ready" if route == "seed" else "complete")
        cleanup(reopened, settings)
        assert old_id not in archive.remote
        assert load_source(reopened, old_id) is None
    finally:
        reopened.close()


def test_replacement_success_does_not_discharge_older_uncertain_same_id(runtime, monkeypatch):
    settings, db, archive = runtime
    row = append(db, "Same deterministic object, distinct requests")
    delayed = []
    successor = []

    def overlap(kwargs):
        delayed.append(kwargs)
        archive.on_retain = None
        db.execute("UPDATE memory_jobs SET lease_deadline=0 WHERE layer='hindsight'")
        db.commit()
        replacement = claim_jobs(db, layers=("hindsight",))[0]
        successor.append(run_memory_claim(db, replacement, SESSION, FIELDS, app_settings=settings))
        raise RuntimeError("Old request still running after replacement success")

    archive.on_retain = overlap
    assert run(db, settings) == "stale_source"
    assert successor == ["complete"]
    old_id = delayed[0]["document_id"]
    assert [item["document_id"] for item in archive.retained] == [old_id, old_id]
    assert load_source(db, old_id) is not None
    change_source(db, row, "rewrite")
    cleanup(db, settings)
    archive.remote[old_id] = delayed[0]
    later = memory_backend.time.time() + 1000
    monkeypatch.setattr(memory_backend.time, "time", lambda: later)
    reopened = db_connect(app_settings=settings)
    try:
        dispatch_once(reopened, settings, startup=True)
        cleanup(reopened, settings)
        assert old_id not in archive.remote
    finally:
        reopened.close()


@pytest.mark.parametrize("replace_cleanup_owner", [False, True])
def test_delete_ack_cannot_erase_later_completion(runtime, monkeypatch, replace_cleanup_owner):
    settings, db, archive = runtime
    row = append(db, "Retain and DELETE cross in flight")
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    results = []
    # Independent processes do not share this RLock. Distinct connections plus
    # no shared lock model that ordering without changing the real protocol.
    monkeypatch.setattr(memory_backend, "hindsight_session_lock", lambda *args: nullcontext())

    def delayed(kwargs):
        entered.set()
        if not release.wait(10):
            raise RuntimeError("Test did not release the delayed response")

    archive.on_retain = delayed

    def retain():
        connection = db_connect(app_settings=settings)
        try:
            results.append(seed_or_worker("seed", connection, settings))
        finally:
            connection.close()
            finished.set()

    thread = threading.Thread(target=retain)
    thread.start()
    try:
        assert entered.wait(10)
        old_id = archive.retained[0]["document_id"]
        change_source(db, row, "rewrite")
        db.execute(
            "UPDATE memory_retired_documents SET lease_token='old-cleaner',lease_deadline=9999999999 "
            "WHERE document_id=?",
            (old_id,),
        )
        db.commit()
        delete = archive.delete_document

        async def crossing_delete(**kwargs):
            await delete(**kwargs)
            release.set()
            if not finished.wait(10):
                raise RuntimeError("Delayed retain did not settle")
            if replace_cleanup_owner:
                db.execute(
                    "UPDATE memory_retired_documents SET lease_token='new-cleaner',lease_deadline=9999999999 "
                    "WHERE document_id=?",
                    (old_id,),
                )
                db.commit()

        monkeypatch.setattr(archive, "delete_document", crossing_delete)
        memory_backend.cleanup_retired_memory_documents(db, "c", "s", app_settings=settings, lease_token="old-cleaner")
        assert results == ["degraded"]
        assert old_id in archive.remote
        state = db.execute(
            "SELECT deleted,lease_token FROM memory_retired_documents WHERE document_id=?", (old_id,)
        ).fetchone()
        assert state == (0, "new-cleaner" if replace_cleanup_owner else "old-cleaner")
        monkeypatch.setattr(archive, "delete_document", delete)
        later = memory_backend.time.time() + 1000
        monkeypatch.setattr(memory_backend.time, "time", lambda: later)
        assert memory_backend.cleanup_retired_memory_documents(
            db, "c", "s", app_settings=settings, lease_token=state[1]
        )
        assert old_id not in archive.remote
    finally:
        release.set()
        thread.join(10)
        assert not thread.is_alive()


def test_recurring_watches_are_bounded_and_yield_to_normal_claims(runtime):
    settings, db, archive = runtime
    append(db, "Fresh canonical work must get a turn")
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
    row = append(db, "Recorded accepted identity")
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
    assert db.execute("SELECT covered_id FROM memory_layer_state WHERE layer='hindsight'").fetchone() == (row,)
    archive.remote.update({"old-invalid": {}, "mapped-only": {}, current.document_id: {}, pending.document_id: {}})
    dispatch_once(db, settings, startup=True)
    cleanup_result = memory_backend.cleanup_retired_memory_documents(db, "c", "gone", app_settings=settings)
    assert cleanup_result
    assert set(archive.remote) == {current.document_id, pending.document_id}
    assert load_source(db, current.document_id) is not None
    assert load_source(db, pending.document_id) is None
    db.close()


def test_public_purge_ack_cannot_erase_completion_after_verification(runtime, monkeypatch):
    settings, db, archive = runtime
    append(db, "Raw completion crosses successful bulk purge")
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    results = []
    monkeypatch.setattr(memory_backend, "hindsight_session_lock", lambda *args: nullcontext())

    def delayed(kwargs):
        entered.set()
        if not release.wait(10):
            raise RuntimeError("Test did not release the delayed retain")

    archive.on_retain = delayed

    def retain():
        connection = db_connect(app_settings=settings)
        try:
            results.append(seed_or_worker("seed", connection, settings))
        finally:
            connection.close()
            finished.set()

    async def after_remote_verification():
        # Bulk deletion has already verified an empty remote listing. Its
        # client-close boundary precedes the public local purge ACK.
        release.set()
        if not finished.wait(10):
            raise RuntimeError("Delayed retain did not settle before purge ACK")

    monkeypatch.setattr(archive, "aclose", after_remote_verification)
    thread = threading.Thread(target=retain)
    thread.start()
    try:
        assert entered.wait(10)
        old_id = archive.retained[0]["document_id"]
        memory.purge_hindsight_session(db, "c", "s", app_settings=settings)
        assert results == ["degraded"]
        assert old_id in archive.remote
        assert db.execute("SELECT 1 FROM memory_archival_attempts WHERE document_id=?", (old_id,)).fetchone() is None
        cleanup(db, settings)
        assert old_id not in archive.remote
        assert load_source(db, old_id) is None
    finally:
        release.set()
        thread.join(10)
        assert not thread.is_alive()
