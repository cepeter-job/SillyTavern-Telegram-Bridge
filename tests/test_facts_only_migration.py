"""Forward retirement preserves exact historical debt and native authority fences."""

import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import pytest
from settings_test_support import make_test_settings
from test_story_memory_scope import append

from bridge import memory_backend
from bridge.memory_fact_store import index_fact_is_current, remember_local_fact
from bridge.memory_store import (
    begin_archival_attempt,
    begin_external_memory_attempt,
    claim_is_current,
    claim_jobs,
    next_source_segment,
    reconcile_archival_attempts,
    reserve_archival_source,
    source_is_valid,
    store_segment,
)
from bridge.migrations import run_migrations
from bridge.schema import SCHEMA_MIGRATIONS, initialize_database_schema
from bridge.sqlite_store import write_transaction


@pytest.fixture
def legacy():
    db = sqlite3.connect(":memory:")
    run_migrations(db, tuple(m for m in SCHEMA_MIGRATIONS if m.version <= 27))
    db.execute(
        "INSERT INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,"
        "world_file,created_at,updated_at) VALUES('c','s','Story','','m','','',1,1)"
    )
    db.commit()
    append(db, "Historical complete source.")
    yield db
    db.close()


def capture_raw(db, suffix="pending", *, valid=2):
    """Synthetic pre-upgrade reservation; no production code sends this object."""
    source = replace(
        next_source_segment(db, "c", "s", "episodes"), layer="hindsight", document_id="historical-raw-" + suffix
    )
    assert reserve_archival_source(db, source)
    if valid != 2:
        db.execute("UPDATE memory_segments SET valid=? WHERE document_id=?", (valid, source.document_id))
        db.commit()
    with write_transaction(db):
        token = begin_archival_attempt(db, source)
    return source, token


def test_upgrade_retires_all_raw_identities_without_changing_native_epoch_or_authority(legacy):
    db = legacy
    raw = [capture_raw(db, "accepted", valid=1), capture_raw(db), capture_raw(db, "invalid", valid=0)]
    db.execute("INSERT INTO hindsight_documents VALUES('c','s','mapped-only','source_segment',1)")
    db.execute("UPDATE memory_layer_state SET purge_epoch=3,source_floor_id=1 WHERE layer='hindsight'")
    db.execute("INSERT INTO meta(key,value) VALUES('hindsight_epoch:c:s','3')")
    db.commit()
    remember_local_fact(db, "c", "s", "Mira", "Current accepted native fact.")
    native = db.execute("SELECT document_id FROM memory_fact_index").fetchone()[0]
    db.execute("INSERT INTO hindsight_documents VALUES('c','s',?,'native_fact',1)", (native,))
    db.execute(
        "INSERT INTO memory_archival_attempts(attempt_token,document_id,chat_id,session_id,kind) "
        "VALUES(?,?,'c','s','native_fact')",
        ("legacy-native:" + native, native),
    )
    db.commit()
    claim = claim_jobs(db, layers=("hindsight",))[0]
    attempts = db.execute("SELECT * FROM memory_archival_attempts ORDER BY attempt_token").fetchall()
    initialize_database_schema(db)
    assert db.execute("SELECT valid FROM memory_segments WHERE layer='hindsight'").fetchall() == [(0,), (0,), (0,)]
    assert {row[0] for row in db.execute("SELECT document_id FROM memory_retired_documents WHERE deleted=0")} == {
        "historical-raw-accepted",
        "historical-raw-pending",
        "historical-raw-invalid",
        "mapped-only",
    }
    assert db.execute("SELECT kind,document_id FROM hindsight_documents").fetchall() == [("native_fact", native)]
    assert db.execute(
        "SELECT purge_epoch,source_floor_id FROM memory_layer_state WHERE layer='hindsight'"
    ).fetchone() == (3, 1)
    assert db.execute("SELECT value FROM meta WHERE key='hindsight_epoch:c:s'").fetchone() == ("3",)
    assert index_fact_is_current(db, native) is not None
    assert db.execute("SELECT * FROM memory_archival_attempts ORDER BY attempt_token").fetchall() == attempts
    assert not claim_is_current(db, claim)
    assert db.execute(
        "SELECT completed_version,dirty_version,lease_token FROM memory_jobs WHERE layer='hindsight'"
    ).fetchone() == (claim.version, claim.version + 1, "")
    assert all(not source_is_valid(db, source) and not store_segment(db, source) for source, _ in raw)
    identity = db.execute("SELECT rewrite_identity FROM memory_layer_state WHERE layer='hindsight'").fetchone()
    initialize_database_schema(db)
    assert db.execute("SELECT rewrite_identity FROM memory_layer_state WHERE layer='hindsight'").fetchone() == identity


def test_upgrade_fences_captured_raw_claim_and_completes_obsolete_jobs(legacy):
    db = legacy
    source, token = capture_raw(db)
    claim = claim_jobs(db, layers=("hindsight",))[0]
    initialize_database_schema(db)
    assert not claim_is_current(db, claim)
    assert not store_segment(db, source)
    assert db.execute(
        "SELECT dirty_version,completed_version,lease_token FROM memory_jobs WHERE layer='hindsight'"
    ).fetchone() == (claim.version, claim.version, "")
    assert db.execute("SELECT finished FROM memory_archival_attempts WHERE attempt_token=?", (token,)).fetchone() == (
        0,
    )


def test_already_invalid_raw_tombstone_does_not_reopen_on_message_mutation(legacy):
    db = legacy
    source, _ = capture_raw(db, valid=0)
    initialize_database_schema(db)
    db.execute("UPDATE memory_retired_documents SET deleted=1")
    db.commit()
    before = db.execute("SELECT deleted,retirement_revision FROM memory_retired_documents").fetchall()
    append(db, "A later current turn.")
    db.execute("UPDATE messages SET content='Revised later turn.' WHERE id=2")
    db.execute("UPDATE messages SET content='Revised original turn.' WHERE id=1")
    db.commit()
    assert db.execute("SELECT deleted,retirement_revision FROM memory_retired_documents").fetchall() == before
    assert not source_is_valid(db, source)


def test_raw_attempt_reconciliation_cannot_exempt_a_surviving_legacy_segment(legacy):
    db = legacy
    source, token = capture_raw(db, valid=1)
    reconcile_archival_attempts(db, now=100)
    assert db.execute(
        "SELECT deleted FROM memory_retired_documents WHERE document_id=?", (source.document_id,)
    ).fetchone() == (0,)
    assert db.execute(
        "SELECT finished,next_check_at FROM memory_archival_attempts WHERE attempt_token=?", (token,)
    ).fetchone() == (0, 400)


def test_exact_cleanup_does_not_protect_obsolete_raw_authority(legacy, monkeypatch):
    db = legacy
    source, _ = capture_raw(db, valid=1)
    db.execute(
        "INSERT INTO memory_retired_documents(chat_id,session_id,document_id) VALUES('c','s',?)", (source.document_id,)
    )
    db.commit()
    deleted = []

    async def delete(**kwargs):
        deleted.append(kwargs["document_id"])

    client = SimpleNamespace(documents=SimpleNamespace(delete_document=delete), close=lambda: None)
    monkeypatch.setattr(memory_backend, "hindsight_client", lambda **kw: client)
    assert memory_backend.cleanup_retired_memory_documents(db, "c", "s", app_settings=make_test_settings())
    assert deleted == [source.document_id]


def test_completed_native_authority_tombstone_survives_upgrade_and_startup(legacy):
    db = legacy
    remember_local_fact(db, "c", "s", "Mira", "Native authority must stay revoked.")
    document = db.execute("SELECT document_id FROM memory_fact_index").fetchone()[0]
    db.execute(
        "INSERT INTO memory_retired_documents(chat_id,session_id,document_id,deleted) VALUES('c','s',?,1)", (document,)
    )
    db.commit()
    with write_transaction(db):
        token = begin_external_memory_attempt(
            db, document_id=document, chat_id="c", session_id="s", session_created_at=1, kind="native_fact"
        )
    for _ in range(2):
        initialize_database_schema(db)
        assert index_fact_is_current(db, document) is None
        assert db.execute(
            "SELECT deleted FROM memory_retired_documents WHERE document_id=?", (document,)
        ).fetchone() == (1,)
        assert db.execute(
            "SELECT finished FROM memory_archival_attempts WHERE attempt_token=?", (token,)
        ).fetchone() == (0,)


def test_native_completion_during_upgrade_keeps_same_epoch_index_pending(legacy, monkeypatch):
    from bridge.memory_workers import run_memory_claim

    db = legacy
    remember_local_fact(db, "c", "s", "Mira", "Valid native fact through upgrade.")
    document = db.execute("SELECT document_id FROM memory_fact_index").fetchone()[0]
    claim = claim_jobs(db, layers=("hindsight",))[0]
    calls = []

    def retain(*args, **kwargs):
        calls.append(args[2])
        if len(calls) == 1:
            initialize_database_schema(db)
        return True

    monkeypatch.setattr(memory_backend, "_retain_with_client", retain)
    assert run_memory_claim(db, claim, {"session_id": "s"}, {}, app_settings=make_test_settings()) == "stale_source"
    assert index_fact_is_current(db, document) is not None
    assert db.execute("SELECT state FROM memory_fact_index").fetchone() == ("pending",)
    assert db.execute("SELECT 1 FROM memory_retired_documents WHERE document_id=?", (document,)).fetchone() is None
    replacement = claim_jobs(db, layers=("hindsight",))[0]
    assert run_memory_claim(db, replacement, {"session_id": "s"}, {}, app_settings=make_test_settings()) == "complete"
    assert calls == [document, document]
