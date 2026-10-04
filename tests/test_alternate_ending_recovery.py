"""Restart recovery resumes admitted branch work without duplicating its target."""

from unittest.mock import patch

import pytest
from test_alternate_ending import branch, close_story
from test_memory_completion_safety import session_db as session_db

from bridge.sqlite_store import write_transaction


def prepared_operation(case):
    from bridge import alternate_ending

    cp = close_story(case)
    _, db, _ = case
    with patch.object(alternate_ending, "restore_checkpoint_state", side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            branch(case, cp)
    return db.execute("SELECT operation_id FROM operations WHERE kind='alternate_ending'").fetchone()[0]


def test_restart_resumes_prepared_local_work_and_returns_same_target(session_db):
    from bridge.alternate_ending import recover_alternate_ending

    op = prepared_operation(session_db)
    _, db, _ = session_db
    assert db.execute("SELECT COUNT(*) FROM narrative_branches").fetchone()[0] == 0
    first = recover_alternate_ending(db, op)
    assert first.applied
    before = db.total_changes
    assert recover_alternate_ending(db, op) == first and db.total_changes == before


def test_startup_recovery_admission_is_bounded_coalesced_and_has_no_inline_network(session_db, monkeypatch):
    from bridge import alternate_ending_runtime as runtime

    op = prepared_operation(session_db)
    config, db, _ = session_db
    jobs = []
    seeded = []
    monkeypatch.setattr(runtime, "submit_background", lambda *a, **k: jobs.append((a, k)) or True)
    monkeypatch.setattr(
        runtime,
        "seed_alternate_ending_memory",
        lambda db, chat, session, **k: seeded.append(session["session_id"]) or "ready",
    )
    assert runtime.queue_alternate_ending_recovery(db, app_settings=config) == 1
    assert runtime.queue_alternate_ending_recovery(db, app_settings=config) == 0
    assert not seeded and len(jobs) == 1
    args, kwargs = jobs.pop()
    args[1](*args[2:], **kwargs)
    assert len(seeded) == 1
    assert db.execute("SELECT state FROM operations WHERE operation_id=?", (op,)).fetchone()[0] == "applied"
    assert runtime.queue_alternate_ending_recovery(db, app_settings=config) == 0


def test_busy_chat_keeps_prepared_branch_recoverable_without_spawning_more_workers(session_db, monkeypatch):
    from bridge import alternate_ending_runtime as runtime
    from bridge.background import chat_job_lock

    op = prepared_operation(session_db)
    config, db, _ = session_db
    jobs = []
    monkeypatch.setattr(runtime, "submit_background", lambda *a, **k: jobs.append((a, k)) or True)
    lock = chat_job_lock("chat")
    lock.acquire()
    try:
        assert runtime.queue_alternate_ending_recovery(db, app_settings=config) == 1
        args, kwargs = jobs.pop()
        args[1](*args[2:], **kwargs)
    finally:
        lock.release()
    assert db.execute("SELECT state FROM operations WHERE operation_id=?", (op,)).fetchone()[0] == "prepared"
    assert runtime.queue_alternate_ending_recovery(db, app_settings=config) == 1
    monkeypatch.setattr(runtime, "seed_alternate_ending_memory", lambda *a, **k: "disabled")
    args, kwargs = jobs.pop()
    args[1](*args[2:], **kwargs)
    assert db.execute("SELECT state FROM operations WHERE operation_id=?", (op,)).fetchone()[0] == "applied"


def test_deleted_origin_before_local_copy_stops_background_retries(session_db, monkeypatch):
    from bridge import alternate_ending_runtime as runtime

    op = prepared_operation(session_db)
    config, db, _ = session_db
    with write_transaction(db):
        db.execute("DELETE FROM sessions WHERE chat_id='chat' AND session_id='s1'")
    jobs = []
    monkeypatch.setattr(runtime, "submit_background", lambda *a, **k: jobs.append((a, k)) or True)
    monkeypatch.setattr(runtime, "seed_alternate_ending_memory", lambda *a, **k: pytest.fail("Seeded missing branch"))
    runtime.queue_alternate_ending_recovery(db, app_settings=config)
    args, kwargs = jobs.pop()
    args[1](*args[2:], **kwargs)
    assert db.execute("SELECT state FROM operations WHERE operation_id=?", (op,)).fetchone()[0] == "failed"
    assert runtime.queue_alternate_ending_recovery(db, app_settings=config) == 0
