"""Local reset must commit without waiting for any provider response."""

import threading
from contextlib import closing
from types import SimpleNamespace

import pytest
from memory_cleanup_test_support import (
    append,
    fact,
    local_service,
    reset,
    run_fact,
    start_call,
)
from memory_cleanup_test_support import (
    cleanup_runtime as cleanup_runtime,
)
from memory_runtime_test_support import isolated_memory_runtime as isolated_memory_runtime

from bridge import memory_backend, message_commands
from bridge.memory_store import claim_jobs
from bridge.memory_workers import run_memory_claim
from bridge.session_core import delete_session_data
from bridge.sqlite_store import write_transaction


def test_reset_commits_queue_before_local_delete_and_survives_reopen(cleanup_runtime):
    rt = cleanup_runtime
    source = append(rt)
    doc = fact(rt)
    seen = []
    queue = local_service().queue_cleanup

    def capture(db, chat, sid):
        seen.append((db.in_transaction, db.execute("SELECT count(*) FROM messages").fetchone()[0]))
        queue(db, chat, sid)

    message_commands.reset_session(
        rt.db,
        "token",
        "c",
        rt.session,
        memory_service=SimpleNamespace(queue_cleanup=capture),
        npc_service=SimpleNamespace(purge_session=lambda *args: None),
        operation_id="reset-once",
    )
    assert seen == [(True, 1)]
    assert not rt.archive.retained and not rt.archive.deleted
    with closing(rt.db_factory()) as reopened:
        assert reopened.execute("SELECT count(*) FROM messages").fetchone()[0] == 0
        assert reopened.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1
        targets = {row[0] for row in reopened.execute("SELECT document_id FROM memory_retired_documents")}
        assert doc in targets
        assert reopened.execute("SELECT value FROM meta WHERE key='hindsight_epoch:c:s'").fetchone() == ("1",)
        assert reopened.execute("SELECT count(*) FROM memory_cleanup_discovery").fetchone() == (1,)
    assert source.document_id not in rt.archive.objects
    reset(rt, "reset-once")
    assert rt.db.execute("SELECT count(*) FROM memory_cleanup_discovery").fetchone() == (1,)


def test_queue_requires_transaction_and_local_failure_rolls_everything_back(cleanup_runtime):
    from bridge.memory_retirement_store import queue_session_memory_cleanup

    rt = cleanup_runtime
    append(rt)
    fact(rt)
    with pytest.raises(RuntimeError, match="transaction"):
        queue_session_memory_cleanup(rt.db, "c", "s")
    before = list(rt.db.iterdump())

    def fail(*args):
        raise RuntimeError("synthetic local failure")

    with pytest.raises(RuntimeError, match="synthetic local"):
        reset(rt, "rollback", SimpleNamespace(purge_session=fail))
    assert list(rt.db.iterdump()) == before


@pytest.mark.parametrize("phase", ["memory_purged", "local_committed"])
def test_legacy_operation_phase_does_not_repeat_generation(cleanup_runtime, phase):
    from bridge.operations import begin_operation, set_operation_phase

    rt = cleanup_runtime
    append(rt)
    begin_operation(rt.db, "legacy", "reset")
    set_operation_phase(rt.db, "legacy", "reset", phase)
    rt.db.commit()
    reset(rt, "legacy")
    reset(rt, "legacy")
    assert rt.db.execute("SELECT count(*) FROM memory_cleanup_discovery").fetchone()[0] == (phase != "local_committed")


@pytest.mark.parametrize("kind", ["raw", "native_fact"])
@pytest.mark.parametrize("action", ["reset", "delete"])
def test_local_lifecycle_completes_while_retain_is_held(cleanup_runtime, kind, action):
    rt = cleanup_runtime
    source = append(rt)
    doc = fact(rt) if kind == "native_fact" else source.document_id
    if kind == "raw":
        from bridge.memory_store import begin_archival_attempt, reserve_archival_source
        from bridge.sqlite_store import write_transaction

        # Historical dispatch captured by an older runtime; the current worker never sends raw text.
        assert reserve_archival_source(rt.db, source)
        with write_transaction(rt.db):
            historical_token = begin_archival_attempt(rt.db, source)
    claim = claim_jobs(rt.db, layers=("hindsight",))[0]
    entered, release = threading.Event(), threading.Event()

    def hold(values):
        entered.set()
        assert release.wait(10)

    rt.archive.before_retain = hold

    def retain(db):
        if kind == "raw":
            from bridge.memory_store import finish_archival_attempt, reconcile_archival_attempts

            hold({})
            rt.archive.objects[doc] = ["session:s"]  # Synthetic late historical completion.
            finish_archival_attempt(db, historical_token)
            reconcile_archival_attempts(db)
            return "stale_source"
        return run_memory_claim(db, claim, rt.session, {"name": "Mira"}, app_settings=rt.config)

    thread, done, values, errors = start_call(rt, retain)
    try:
        assert entered.wait(5), (values, errors)
        assert rt.db.execute("SELECT kind FROM memory_archival_attempts WHERE document_id=?", (doc,)).fetchone() == (
            kind,
        )
        if action == "reset":
            reset(rt)
        else:
            assert delete_session_data(rt.db, "c", "s", "other", memory_service=local_service()) == (True, "deleted")
        assert not done.is_set()
        assert not rt.db.execute("SELECT 1 FROM messages").fetchone()
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive() and not errors
    assert values == ["stale_source"]
    assert rt.db.execute("SELECT deleted FROM memory_retired_documents WHERE document_id=?", (doc,)).fetchone() == (0,)
    assert not rt.db.execute("SELECT 1 FROM hindsight_documents WHERE document_id=?", (doc,)).fetchone()


def test_native_timeout_successor_cannot_resolve_oldest_unknown_attempt(cleanup_runtime):
    rt = cleanup_runtime
    append(rt)
    doc = fact(rt)
    rt.archive.fail_retain = True
    assert run_fact(rt) == "retain_failed"
    oldest = rt.db.execute("SELECT attempt_token FROM memory_archival_attempts WHERE document_id=?", (doc,)).fetchone()[
        0
    ]
    rt.archive.fail_retain = False
    rt.db.execute("UPDATE memory_jobs SET next_attempt_at=0")
    rt.db.commit()
    assert run_fact(rt) == "complete"
    assert rt.db.execute(
        "SELECT attempt_token,finished FROM memory_archival_attempts WHERE document_id=?", (doc,)
    ).fetchall() == [(oldest, 0)]
    reset(rt)
    # Known exact cleanup is allowed while discovery is deferred.
    rt.db.execute("UPDATE memory_cleanup_discovery SET phase='verify',next_attempt_at=99999999999")
    rt.db.commit()
    memory_backend.cleanup_retired_memory_documents(rt.db, "c", "s", app_settings=rt.config)
    assert rt.db.execute("SELECT deleted FROM memory_retired_documents WHERE document_id=?", (doc,)).fetchone() == (0,)
    rt.archive.objects[doc] = ["session:s"]  # arbitrarily late oldest server write
    rt.db.execute("UPDATE memory_retired_documents SET next_attempt_at=0")
    rt.db.commit()
    memory_backend.cleanup_retired_memory_documents(rt.db, "c", "s", app_settings=rt.config)
    assert doc not in rt.archive.objects
    assert rt.db.execute(
        "SELECT finished FROM memory_archival_attempts WHERE attempt_token=?", (oldest,)
    ).fetchone() == (0,)


def test_reset_and_revision_reopen_while_delete_response_is_held(cleanup_runtime):
    from bridge.memory_retirement_store import reopen_retirement

    rt = cleanup_runtime
    append(rt)
    rt.db.execute("INSERT INTO memory_retired_documents(chat_id,session_id,document_id) VALUES('c','s','old')")
    rt.db.commit()
    entered, release = threading.Event(), threading.Event()

    def hold(doc):
        entered.set()
        assert release.wait(10)

    rt.archive.after_delete = hold
    thread, _, _, errors = start_call(
        rt, lambda db: memory_backend.cleanup_retired_memory_documents(db, "c", "s", app_settings=rt.config)
    )
    try:
        assert entered.wait(5)
        reset(rt)
        with write_transaction(rt.db):
            reopen_retirement(rt.db, "c", "s", "old")
        revision = rt.db.execute(
            "SELECT retirement_revision FROM memory_retired_documents WHERE document_id='old'"
        ).fetchone()[0]
    finally:
        release.set()
        thread.join(10)
    assert not errors and not thread.is_alive()
    assert rt.db.execute(
        "SELECT deleted,next_attempt_at,retirement_revision,lease_token FROM memory_retired_documents "
        "WHERE document_id='old'"
    ).fetchone() == (0, 0, revision, "")


def test_attempt_at_delete_start_prevents_terminal_ack_after_it_resolves(cleanup_runtime):
    from bridge.memory_store import begin_external_memory_attempt, finish_archival_attempt, resolve_archival_attempt

    rt = cleanup_runtime
    with write_transaction(rt.db):
        rt.db.execute("INSERT INTO memory_retired_documents(chat_id,session_id,document_id) VALUES('c','s','old')")
        token = begin_external_memory_attempt(
            rt.db, document_id="old", chat_id="c", session_id="s", session_created_at=1, kind="raw"
        )

    def finish_after_delete(doc):
        rt.archive.objects[doc] = ["session:s"]
        finish_archival_attempt(rt.db, token)
        with write_transaction(rt.db):
            resolve_archival_attempt(rt.db, token)

    rt.archive.after_delete = finish_after_delete
    memory_backend.cleanup_retired_memory_documents(rt.db, "c", "s", app_settings=rt.config)
    assert (
        rt.db.execute(
            "SELECT deleted,next_attempt_at FROM memory_retired_documents WHERE document_id='old'"
        ).fetchone()[0]
        == 0
    )
