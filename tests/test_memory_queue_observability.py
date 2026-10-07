"""Read-only queue metrics use the same predicates as durable memory claims."""

import sqlite3
import time
from types import SimpleNamespace

import pytest

from bridge.memory_store import acknowledge_job, claim_jobs
from bridge.schema import initialize_database_schema


@pytest.fixture
def db(tmp_path):
    connection = sqlite3.connect(tmp_path / "queue.sqlite")
    initialize_database_schema(connection)
    yield connection
    connection.close()


def job(db, chat, layer, *, message_at, pending_at):
    db.execute(
        "INSERT INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,world_file,"
        "created_at,updated_at) "
        "VALUES(?,'s','Story','','m','','',1,1)",
        (chat,),
    )
    db.execute(
        "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,'s','user','synthetic',?)",
        (chat, message_at),
    )
    db.execute("DELETE FROM memory_jobs WHERE chat_id=? AND layer<>?", (chat, layer))
    db.execute("UPDATE memory_jobs SET pending_since=? WHERE chat_id=?", (pending_at, chat))
    db.commit()


def test_eligible_backlog_excludes_active_backoff_idle_parked_and_disabled(db):
    # Counting dirty rows as runnable backlog incorrectly includes five distinct noneligible states.
    from bridge.memory_queue import queue_counters

    now = 200000.0
    for chat, layer, message_at, pending_at in (
        ("eligible", "episodes", now - 10, now - 40),
        ("future", "episodes", now - 10, now - 900),
        ("idle", "episodes", now - 86401, now - 900),
        ("parked", "episodes", now - 10, now - 900),
        ("exempt", "hindsight", 1, now - 20),
        ("disabled", "curator", now - 10, now - 900),
        ("active", "summary", now - 10, now - 900),
    ):
        job(db, chat, layer, message_at=message_at, pending_at=pending_at)
    db.execute("UPDATE memory_jobs SET next_attempt_at=? WHERE chat_id='future'", (now + 50,))
    db.execute("UPDATE memory_jobs SET attempts=8,last_error='work_failed' WHERE chat_id IN ('parked','exempt')")
    db.execute("INSERT INTO meta(key,value) VALUES('memory_mode:disabled','off')")
    db.execute("UPDATE memory_jobs SET lease_token='active-token',lease_deadline=? WHERE chat_id='active'", (now + 1,))
    db.commit()
    changed = db.total_changes
    counters = queue_counters(db, now=now)
    assert db.total_changes == changed
    assert counters == {
        "memory.jobs.pending": 7,
        "memory.jobs.eligible": 2,
        "memory.jobs.leased": 1,
        "memory.jobs.backoff": 1,
        "memory.jobs.inactive": 1,
        "memory.jobs.parked": 1,
        "memory.jobs.disabled": 1,
        "memory.jobs.eligible_age_unknown": 0,
        "memory.jobs.oldest_eligible_age_ms": 40000,
    }
    assert claim_jobs(db, now=now, autonomous=True) == []  # the real executor has one active lease
    db.execute("UPDATE memory_jobs SET completed_version=dirty_version,lease_token='' WHERE chat_id='active'")
    db.commit()
    first = claim_jobs(db, now=now, autonomous=True)[0]
    assert (first.chat_id, first.layer) == ("eligible", "episodes")
    assert acknowledge_job(db, first)
    second = claim_jobs(db, now=now, autonomous=True)[0]
    assert (second.chat_id, second.layer) == ("exempt", "hindsight")
    assert acknowledge_job(db, second)
    assert claim_jobs(db, now=now, autonomous=True) == []


def test_expired_lease_is_recoverable_backlog_not_active(db):
    # An expired lease is released by the scheduler before its eligibility query.
    from bridge.memory_queue import queue_counters

    job(db, "c", "episodes", message_at=100, pending_at=90)
    db.execute("UPDATE memory_jobs SET lease_token='expired',lease_deadline=100")
    db.commit()
    counters = queue_counters(db, now=110)
    assert counters["memory.jobs.eligible"] == 1 and counters["memory.jobs.leased"] == 0
    claim = claim_jobs(db, now=110, autonomous=True)[0]
    assert claim.chat_id == "c" and claim.token != "expired"


def test_missing_session_incarnation_and_completed_work_are_not_backlog(db):
    from bridge.memory_queue import queue_counters

    job(db, "stale", "summary", message_at=100, pending_at=90)
    job(db, "complete", "summary", message_at=100, pending_at=90)
    db.execute("UPDATE memory_jobs SET session_created_at=99 WHERE chat_id='stale'")
    db.execute("UPDATE memory_jobs SET completed_version=dirty_version WHERE chat_id='complete'")
    db.commit()
    counters = queue_counters(db, now=110)
    assert counters["memory.jobs.pending"] == 1 and counters["memory.jobs.inactive"] == 1
    assert counters["memory.jobs.eligible"] == 0 and counters["memory.jobs.oldest_eligible_age_ms"] == 0
    assert claim_jobs(db, now=110, autonomous=True) == []


def test_unknown_pending_origin_does_not_fabricate_oldest_age(db):
    from bridge.memory_queue import queue_counters

    job(db, "known", "summary", message_at=100, pending_at=90)
    job(db, "unknown", "summary", message_at=100, pending_at=None)
    counters = queue_counters(db, now=110)
    assert counters["memory.jobs.eligible"] == 2
    assert counters["memory.jobs.eligible_age_unknown"] == 1
    assert counters["memory.jobs.oldest_eligible_age_ms"] == -1


def test_pending_origin_is_recorded_at_enqueue_not_session_or_message_age(db):
    # Ancient source timestamps must never turn a newly enqueued job into old backlog.
    before = time.time()
    db.execute(
        "INSERT INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,world_file,"
        "created_at,updated_at) "
        "VALUES('c','s','Story','','m','','',1,1)"
    )
    db.execute("INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('c','s','user','synthetic',2)")
    db.commit()
    pending = db.execute("SELECT pending_since FROM memory_jobs WHERE layer='hindsight'").fetchone()[0]
    assert before - 0.01 <= pending <= time.time() + 0.01
    db.execute("INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('c','s','user','later',3)")
    db.commit()
    assert db.execute("SELECT pending_since FROM memory_jobs WHERE layer='hindsight'").fetchone()[0] == pending
    claim = claim_jobs(db, layers=("hindsight",))[0]
    assert acknowledge_job(db, claim)
    db.execute("UPDATE memory_jobs SET pending_since=1 WHERE layer='hindsight'")
    db.commit()
    before = time.time()
    db.execute("INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('c','s','user','new cycle',4)")
    db.commit()
    current = db.execute("SELECT pending_since FROM memory_jobs WHERE layer='hindsight'").fetchone()[0]
    assert before - 0.01 <= current <= time.time() + 0.01


@pytest.mark.parametrize("layer", ["hindsight", "curator"])
def test_hindsight_and_curator_mode_checks_are_per_chat(db, layer):
    # The previous layer-wide prefilter could admit disabled work because another actor enabled its layer.
    from bridge.memory_queue import queue_counters

    job(db, "off", layer, message_at=99999, pending_at=90)
    job(db, "on", layer, message_at=99999, pending_at=95)
    db.execute("INSERT INTO meta(key,value) VALUES('memory_mode:off','off')")
    db.commit()
    counters = queue_counters(db, now=100000)
    assert counters["memory.jobs.eligible"] == 1 and counters["memory.jobs.disabled"] == 1
    claim = claim_jobs(db, now=100000, autonomous=True)[0]
    assert claim.chat_id == "on"


@pytest.mark.parametrize("enabled_peer", [False, True])
def test_dispatch_skips_disabled_retention_without_consuming_its_retry(db, enabled_peer):
    from bridge.memory_workers import dispatch_memory_backlog

    now = time.time()
    job(db, "a-off", "hindsight", message_at=1, pending_at=now)
    db.execute("INSERT INTO meta(key,value) VALUES('memory_mode:a-off','off')")
    if enabled_peer:
        job(db, "z-on", "hindsight", message_at=1, pending_at=now)
    db.commit()
    services = SimpleNamespace(background=SimpleNamespace(submit=lambda *_a: True))
    assert dispatch_memory_backlog(services, db) == int(enabled_peer)
    assert db.execute("SELECT attempts,lease_token,last_error FROM memory_jobs WHERE chat_id='a-off'").fetchone() == (
        0,
        "",
        "",
    )
    if enabled_peer:
        assert db.execute("SELECT attempts,lease_token<>'' FROM memory_jobs WHERE chat_id='z-on'").fetchone() == (1, 1)


def test_memory_off_does_not_block_retirement_cleanup(db):
    from bridge.memory_workers import dispatch_memory_backlog

    job(db, "off", "hindsight", message_at=1, pending_at=time.time())
    db.execute("INSERT INTO meta(key,value) VALUES('memory_mode:off','off')")
    db.execute("INSERT INTO memory_retired_documents(chat_id,session_id,document_id) VALUES('off','s','retired')")
    db.commit()
    services = SimpleNamespace(background=SimpleNamespace(submit=lambda *_a: True))
    assert dispatch_memory_backlog(services, db) == 1
    assert db.execute("SELECT lease_token<>'' FROM memory_retired_documents").fetchone() == (1,)
    assert db.execute("SELECT attempts,lease_token FROM memory_jobs").fetchone() == (0, "")
