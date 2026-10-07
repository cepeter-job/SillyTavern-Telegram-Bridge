"""Durable source coverage survives rollback, interrupted execution and rewrites."""

import sqlite3

import pytest

from bridge.schema import initialize_database_schema
from bridge.sqlite_store import write_transaction


@pytest.fixture
def db():
    connection = sqlite3.connect(":memory:")
    initialize_database_schema(connection)
    for sid, created in [("s", 1.0), ("target", 2.0)]:
        connection.execute(
            "INSERT INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,"
            "world_file,created_at,updated_at) VALUES('c',?,'Story','','m','','',?,?)",
            (sid, created, created),
        )
    connection.commit()
    yield connection
    connection.close()


def append(db, text="hello", sid="s"):
    cursor = db.execute(
        "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('c',?,'user',?,3)",
        (sid, text),
    )
    db.commit()
    return cursor.lastrowid


def test_transcript_rollback_has_no_durable_job(db):
    with pytest.raises(RuntimeError), write_transaction(db):
        db.execute("INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('c','s','user','x',3)")
        raise RuntimeError("rollback")
    assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM memory_jobs").fetchone()[0] == 0


def test_append_dirties_every_independent_layer_and_delivery_does_not(db):
    append(db)
    assert set(db.execute("SELECT layer,dirty_version FROM memory_jobs")) == {
        (layer, 1) for layer in ("hindsight", "episodes", "summary", "scene", "npc", "curator")
    }
    db.execute("UPDATE messages SET telegram_message_id='42'")
    db.commit()
    assert {row[0] for row in db.execute("SELECT dirty_version FROM memory_jobs")} == {1}


def test_claim_ack_keeps_append_during_work_and_active_lease(db):
    from bridge.memory_store import acknowledge_job, claim_jobs, recover_expired_jobs

    append(db)
    claim = claim_jobs(db, now=10, layers=("hindsight",), limit=1)[0]
    append(db, "later")
    assert recover_expired_jobs(db, now=11) == 0
    assert claim_jobs(db, now=11, layers=("hindsight",)) == []
    assert acknowledge_job(db, claim)
    row = db.execute(
        "SELECT dirty_version,completed_version,lease_token FROM memory_jobs WHERE layer='hindsight'"
    ).fetchone()
    assert row == (2, 1, "")
    assert len(claim_jobs(db, now=12, layers=("hindsight",))) == 1


def test_expired_lease_cannot_ack_recovered_claim(db):
    from bridge.memory_store import acknowledge_job, claim_jobs, recover_expired_jobs

    append(db)
    old = claim_jobs(db, now=10, lease_seconds=5, layers=("episodes",))[0]
    assert recover_expired_jobs(db, now=14) == 0
    assert recover_expired_jobs(db, now=15) == 1
    new = claim_jobs(db, now=16, layers=("episodes",))[0]
    assert not acknowledge_job(db, old)
    assert acknowledge_job(db, new)


def test_failed_attempt_preserves_pending_and_safe_error(db):
    from bridge.memory_store import claim_jobs, fail_job

    append(db)
    claim = claim_jobs(db, now=10, layers=("hindsight",))[0]
    assert fail_job(db, claim, "retain_failed", now=10)
    assert db.execute("SELECT completed_version,last_error FROM memory_jobs WHERE layer='hindsight'").fetchone() == (
        0,
        "retain_failed",
    )
    assert claim_jobs(db, now=11, layers=("hindsight",)) == []
    assert len(claim_jobs(db, now=100, layers=("hindsight",))) == 1


def test_deferred_attempt_is_immediately_claimable_without_failure_backoff(db):
    from bridge.memory_store import claim_jobs, fail_job

    append(db)
    claim = claim_jobs(db, now=10, layers=("hindsight",))[0]
    assert fail_job(db, claim, "deferred", now=10, deferred=True)
    assert db.execute("SELECT last_error,next_attempt_at FROM memory_jobs WHERE layer='hindsight'").fetchone() == (
        "deferred",
        10,
    )
    assert len(claim_jobs(db, now=10, layers=("hindsight",))) == 1


def test_segments_include_oldest_over_100_and_every_oversized_part(db):
    from bridge.memory_store import next_source_segment, source_is_valid, store_segment

    first = append(db, "α" * 12000)
    for number in range(105):
        append(db, f"tail {number}")
    contents = []
    identifiers = []
    for _ in range(200):
        segment = next_source_segment(db, "c", "s", "episodes", max_chars=1000)
        if segment is None:
            break
        assert len(segment.content) <= 1000
        assert source_is_valid(db, segment)
        contents.append(segment.content)
        identifiers.append(segment.document_id)
        store_segment(db, segment)
    assert len(identifiers) > 100
    assert len(set(identifiers)) == len(identifiers)
    assert sum(text.count("α") for text in contents) == 12000
    assert all(any(f"tail {number}" in text for text in contents) for number in range(105))
    assert first == 1
    assert next_source_segment(db, "c", "s", "episodes", max_chars=1000) is None


@pytest.mark.parametrize("mutation", ["edit", "delete", "move"])
def test_rewrite_invalidates_overlapping_and_later_sources(db, mutation):
    from bridge.memory_store import next_source_segment, source_is_valid, store_segment

    append(db, "old")
    append(db, "next")
    one = next_source_segment(db, "c", "s", "episodes")
    store_segment(db, one)
    two = next_source_segment(db, "c", "s", "episodes")
    store_segment(db, two)
    if mutation == "edit":
        db.execute("UPDATE messages SET content='changed' WHERE id=1")
    elif mutation == "delete":
        db.execute("DELETE FROM messages WHERE id=1")
    else:
        db.execute("UPDATE messages SET session_id='target' WHERE id=1")
    db.commit()
    assert not source_is_valid(db, one)
    assert not source_is_valid(db, two)
    assert db.execute("SELECT count(*) FROM memory_segments WHERE valid=1").fetchone()[0] == 0
    if mutation == "move":
        assert db.execute("SELECT count(*) FROM memory_jobs WHERE session_id='target'").fetchone()[0] == 6


def test_normal_append_preserves_earlier_source(db):
    from bridge.memory_store import next_source_segment, source_is_valid, store_segment

    append(db)
    source = next_source_segment(db, "c", "s", "episodes")
    store_segment(db, source)
    append(db, "new")
    assert source_is_valid(db, source)
    assert next_source_segment(db, "c", "s", "episodes").start_id == 2


def test_delete_session_before_messages_never_creates_ghost_jobs(db):
    from bridge.memory_store import acknowledge_job, claim_jobs

    append(db)
    claim = claim_jobs(db, now=10, layers=("hindsight",))[0]
    db.execute("DELETE FROM sessions WHERE session_id='s'")
    db.execute("DELETE FROM messages WHERE session_id='s'")
    db.commit()
    assert db.execute("SELECT count(*) FROM memory_jobs").fetchone()[0] == 0
    db.execute(
        "INSERT INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,"
        "world_file,created_at,updated_at) VALUES('c','s','New','','m','','',10,10)"
    )
    db.commit()
    append(db, "new")
    assert not acknowledge_job(db, claim)
    assert db.execute("SELECT session_created_at FROM memory_jobs LIMIT 1").fetchone()[0] == 10


def test_source_retry_has_stable_id_and_purge_invalidates(db):
    from bridge.memory_store import invalidate_memory, next_source_segment, source_is_valid

    append(db)
    one = next_source_segment(db, "c", "s", "episodes")
    assert next_source_segment(db, "c", "s", "episodes").document_id == one.document_id
    invalidate_memory(db, "c", "s", purge_epoch=1)
    assert not source_is_valid(db, one)
    assert next_source_segment(db, "c", "s", "episodes").document_id != one.document_id


def test_migration_backfills_existing_rows_once_and_retires_legacy_documents():
    from bridge.migrations import run_migrations
    from bridge.schema import SCHEMA_MIGRATIONS

    db = sqlite3.connect(":memory:")
    run_migrations(db, tuple(migration for migration in SCHEMA_MIGRATIONS if migration.version < 20))
    db.execute(
        "INSERT INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,"
        "world_file,created_at,updated_at) VALUES('c','s','Story','','m','','',1,1)"
    )
    db.execute("INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('c','s','user','legacy',3)")
    db.execute("INSERT INTO hindsight_documents VALUES('c','s','old-fixed','conversation',3)")
    db.commit()
    initialize_database_schema(db)
    assert db.execute("SELECT count(*) FROM memory_jobs").fetchone()[0] == 6
    assert db.execute("SELECT deleted FROM memory_retired_documents WHERE document_id='old-fixed'").fetchone() == (0,)
    versions = dict(db.execute("SELECT layer,dirty_version FROM memory_jobs"))
    assert versions == {"hindsight": 1, "episodes": 2, "summary": 3, "scene": 3, "npc": 2, "curator": 2}
    initialize_database_schema(db)
    assert dict(db.execute("SELECT layer,dirty_version FROM memory_jobs")) == versions
    db.close()


def test_invalid_source_is_retired_before_remote_cleanup(db):
    from bridge.memory_store import next_source_segment, store_segment

    append(db)
    source = next_source_segment(db, "c", "s", "hindsight")
    store_segment(db, source)
    db.execute("UPDATE messages SET content='rewrite'")
    db.commit()
    assert db.execute(
        "SELECT deleted FROM memory_retired_documents WHERE document_id=?", (source.document_id,)
    ).fetchone() == (0,)


def test_next_part_does_not_rescan_completed_prefix(db):
    from bridge.memory_store import next_source_segment, store_segment

    for n in range(150):
        append(db, f"prefix {n}")
        store_segment(db, next_source_segment(db, "c", "s", "episodes"))
    append(db, "uncovered")
    statements = []
    db.set_trace_callback(statements.append)
    source = next_source_segment(db, "c", "s", "episodes")
    db.set_trace_callback(None)
    assert source.start_id == 151
    assert len(statements) <= 5


def test_direct_rewrite_resets_native_npc_coverage(db):
    append(db)
    db.execute("INSERT INTO npc_extraction_state VALUES('c','s',1,3)")
    db.commit()
    db.execute("UPDATE messages SET content='rewritten'")
    db.commit()
    assert db.execute("SELECT updated_through_rowid FROM npc_extraction_state").fetchone()[0] == 0


def test_ack_cannot_advance_rewritten_claim(db):
    from bridge.memory_store import acknowledge_job, claim_jobs

    append(db)
    claim = claim_jobs(db, now=10, layers=("hindsight",))[0]
    db.execute("UPDATE messages SET content='changed'")
    db.commit()
    assert not acknowledge_job(db, claim)
    assert db.execute("SELECT completed_version FROM memory_jobs WHERE layer='hindsight'").fetchone()[0] == 0


def test_session_delete_retires_explicit_mappings(db):
    append(db)
    db.execute("INSERT INTO hindsight_documents VALUES('c','s','explicit-old','explicit',3)")
    db.commit()
    db.execute("DELETE FROM sessions WHERE session_id='s'")
    db.commit()
    assert db.execute("SELECT count(*) FROM hindsight_documents").fetchone()[0] == 0
    assert db.execute("SELECT deleted FROM memory_retired_documents WHERE document_id='explicit-old'").fetchone() == (
        0,
    )


def test_forward_migration_respects_existing_external_purge_epoch():
    from bridge.memory_store import claim_jobs, external_memory_boundary
    from bridge.migrations import run_migrations
    from bridge.schema import SCHEMA_MIGRATIONS

    db = sqlite3.connect(":memory:")
    run_migrations(db, tuple(migration for migration in SCHEMA_MIGRATIONS if migration.version < 20))
    db.execute(
        "INSERT INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,"
        "world_file,created_at,updated_at) VALUES('c','s','Story','','m','','',1,1)"
    )
    db.execute("INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('c','s','user','old',3)")
    db.execute("INSERT INTO meta VALUES('hindsight_epoch:c:s','2')")
    db.commit()
    initialize_database_schema(db)
    assert external_memory_boundary(db, "c", "s") == {"session_created_at": 1.0, "purge_epoch": 2, "source_floor_id": 1}
    assert claim_jobs(db, layers=("hindsight",)) == []
    assert len(claim_jobs(db, layers=("episodes",))) == 1
    db.close()


@pytest.mark.parametrize("rollback", [False, True])
def test_external_purge_retires_all_mappings_atomically_without_losing_cleanup_ids(db, rollback):
    from bridge.memory_store import external_memory_boundary, purge_external_memory

    append(db)
    db.executemany(
        "INSERT INTO hindsight_documents VALUES('c','s',?,?,3)",
        [("explicit-old", "explicit"), ("source-old", "source_segment"), ("curated-old", "curated")],
    )
    db.commit()
    try:
        with write_transaction(db):
            purge_external_memory(db, "c", "s", purge_epoch=1)
            if rollback:
                raise RuntimeError("abort purge")
    except RuntimeError:
        assert rollback
    assert db.execute("SELECT count(*) FROM hindsight_documents").fetchone()[0] == 3
    assert set(db.execute("SELECT document_id,deleted FROM memory_retired_documents")) == (
        set() if rollback else {("explicit-old", 0), ("source-old", 0), ("curated-old", 0)}
    )
    assert external_memory_boundary(db, "c", "s")["purge_epoch"] == (0 if rollback else 1)
