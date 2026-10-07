"""Upgrade existing queues without inventing historical enqueue times."""

import sqlite3
import time

from bridge.memory_queue import queue_counters
from bridge.migrations import run_migrations
from bridge.schema import SCHEMA_MIGRATIONS, initialize_database_schema


def test_upgrade_keeps_historical_cycle_unknown_until_completed_then_enqueued(tmp_path):
    db = sqlite3.connect(tmp_path / "upgrade.sqlite")
    try:
        run_migrations(db, tuple(migration for migration in SCHEMA_MIGRATIONS if migration.version <= 30))
        db.execute(
            "INSERT INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,world_file,"
            "created_at,updated_at) "
            "VALUES('c','s','Story','','m','','',1,1)"
        )
        db.execute("INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('c','s','user','legacy',2)")
        db.commit()
        initialize_database_schema(db)
        row = db.execute("SELECT pending_since,dirty_version FROM memory_jobs WHERE layer='hindsight'").fetchone()
        assert row == (None, 1)
        db.execute("INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('c','s','user','new',3)")
        db.commit()
        pending, version = db.execute(
            "SELECT pending_since,dirty_version FROM memory_jobs WHERE layer='hindsight'"
        ).fetchone()
        assert (pending, version) == (None, 2)
        counters = queue_counters(db, now=time.time())
        assert counters["memory.jobs.eligible_age_unknown"] > 0
        assert counters["memory.jobs.oldest_eligible_age_ms"] == -1
        db.execute("UPDATE memory_jobs SET completed_version=dirty_version")
        before = time.time()
        db.execute("INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('c','s','user','next',4)")
        db.commit()
        pending = db.execute("SELECT pending_since FROM memory_jobs WHERE layer='hindsight'").fetchone()[0]
        assert before - 0.01 <= pending <= time.time() + 0.01
        assert queue_counters(db, now=time.time())["memory.jobs.eligible_age_unknown"] == 0
        initialize_database_schema(db)
        assert db.execute("SELECT pending_since FROM memory_jobs WHERE layer='hindsight'").fetchone()[0] == pending
    finally:
        db.close()
