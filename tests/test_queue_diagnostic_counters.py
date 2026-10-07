"""Incident counters read a synthetic queue without migrations, writes or content."""

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from functools import partial

from bridge.memory_diagnostics import MemoryDiagnostics
from bridge.schema import initialize_database_schema


def test_incident_report_includes_real_read_only_queue_counters(tmp_path):
    # Failing to wire the callback leaves only executor counts, hiding durable backlog.
    from bridge.diagnostic_counters import memory_queue_counters

    path = tmp_path / "queue.sqlite"
    with closing(sqlite3.connect(path)) as db:
        initialize_database_schema(db)
        db.execute(
            "INSERT INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,world_file,"
            "created_at,updated_at) VALUES('private-chat','s','private title','','m','','',1,1)"
        )
        db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) "
            "VALUES('private-chat','s','user','private story text',1)"
        )
        db.execute("DELETE FROM memory_jobs WHERE layer<>'hindsight'")
        db.commit()
        before = db.execute("SELECT * FROM memory_jobs").fetchall()
        diagnostics = MemoryDiagnostics(
            tmp_path / "home",
            {"SILLYTAVERN_MEMORY_DIAGNOSTICS": "1"},
            safe_counters=lambda: {"background.active_total": 0},
            queue_counters=partial(memory_queue_counters, path),
        )
        report = diagnostics._build_report(0, datetime.now(timezone.utc))
        assert report["safe_counters"]["background.active_total"] == 0
        assert report["safe_counters"]["memory.jobs.eligible"] == 1
        assert report["safe_counters"]["memory.jobs.leased"] == 0
        assert db.execute("SELECT * FROM memory_jobs").fetchall() == before
        assert not any(
            secret in str(report["safe_counters"]) for secret in ("private-chat", "private title", "private story text")
        )


def test_missing_database_is_not_created_for_diagnostics(tmp_path):
    from bridge.diagnostic_counters import memory_queue_counters

    path = tmp_path / "missing.sqlite"
    assert memory_queue_counters(path) == {"memory.jobs.available": False}
    assert not path.exists()


def test_disabled_diagnostics_never_open_queue_callback(tmp_path):
    # Merely enabling performance logs must not activate incident diagnostics.
    def forbidden():
        raise AssertionError("Disabled diagnostics read the queue")

    diagnostics = MemoryDiagnostics(tmp_path / "home", {}, queue_counters=forbidden)
    assert diagnostics.sample_once().value == "disabled"
