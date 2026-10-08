"""Read-only summary recovery planning is based on canonical queue predicates."""

import json
import sqlite3
import subprocess
import sys
import time

import pytest

from bridge.schema import initialize_database_schema
from bridge.summary_recovery_status import summary_recovery_snapshot


@pytest.fixture
def db(tmp_path):
    connection = sqlite3.connect(":memory:")
    initialize_database_schema(connection)
    yield connection
    connection.close()


def add_session(db, owner, *, now=200000):
    db.execute(
        "INSERT INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,world_file,"
        "created_at,updated_at) VALUES(?,'s','Story','','m','','',1,1)",
        (owner,),
    )
    db.execute(
        "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,'s','user',?,?)",
        (owner, "PRIVATE_CANONICAL_SCENE_STORY", now - 10),
    )
    db.commit()


def test_parked_invalidated_summary_is_not_reported_ready_and_snapshot_is_read_only(db):
    now = 200000.0
    add_session(db, "parked", now=now)
    db.execute(
        "UPDATE memory_jobs SET attempts=313,last_error='work_failed',next_attempt_at=0 "
        "WHERE chat_id='parked' AND layer='summary'"
    )
    db.execute("UPDATE memory_layer_state SET invalidated_from_id=1 WHERE chat_id='parked' AND layer='summary'")
    db.commit()
    changes = db.total_changes
    result = summary_recovery_snapshot(db, now=now)
    assert db.total_changes == changes
    assert result["summary"]["layers"]["invalidated"] == 1
    assert result["summary"]["jobs"]["pending"] == 1
    assert result["summary"]["jobs"]["parked"] == 1
    assert result["summary"]["jobs"]["manual_due"] == 1
    assert result["summary"]["jobs"]["eligible"] == 0
    assert result["approval_ready"] is False
    assert "PRIVATE_CANONICAL_SCENE_STORY" not in json.dumps(result)
    assert '"chat_id": "parked"' not in json.dumps(result)


def test_bounded_categories_distinguish_leased_backoff_and_invalid_incarnation(db):
    now = 200000.0
    for owner in ("due", "backoff", "lease", "stale"):
        add_session(db, owner, now=now)
    db.execute("UPDATE memory_jobs SET next_attempt_at=? WHERE chat_id='backoff' AND layer='summary'", (now + 60,))
    db.execute(
        "UPDATE memory_jobs SET lease_token='token',lease_deadline=? WHERE chat_id='lease' AND layer='summary'",
        (now + 60,),
    )
    db.execute("UPDATE memory_jobs SET session_created_at=99 WHERE chat_id='stale' AND layer='summary'")
    db.commit()
    result = summary_recovery_snapshot(db, now=now)
    jobs = result["summary"]["jobs"]
    assert jobs["pending"] == 4
    assert jobs["eligible"] == 1
    assert jobs["manual_due"] == 1
    assert jobs["backoff"] == 1
    assert jobs["leased"] == 1
    assert jobs["inactive"] == 1
    assert result["approval_ready"] is False


def test_completed_but_invalidated_summary_never_claimed_healthy(db):
    add_session(db, "completed")
    db.execute("UPDATE memory_jobs SET completed_version=dirty_version WHERE chat_id='completed'")
    db.execute("UPDATE memory_layer_state SET invalidated_from_id=1 WHERE chat_id='completed' AND layer='summary'")
    db.commit()
    result = summary_recovery_snapshot(db, now=200000)
    assert result["summary"]["jobs"]["pending"] == 0
    assert result["summary"]["layers"]["invalidated"] == 1
    assert result["summary"]["layers"]["total"] == 1
    assert result["approval_ready"] is False


def test_cli_opens_sqlite_read_only_and_writes_only_bounded_counts(db, tmp_path):
    add_session(db, "secret-owner", now=time.time())
    db.execute("UPDATE memory_jobs SET attempts=313,last_error='work_failed' WHERE layer='summary'")
    db.commit()
    out = tmp_path / "snapshot.json"
    disk = sqlite3.connect(tmp_path / "summary.sqlite3")
    db.backup(disk)
    disk.close()
    original_changes = db.total_changes
    proc = subprocess.run(
        [
            sys.executable,
            "tools/inspect_summary_recovery.py",
            "--database",
            str(tmp_path / "summary.sqlite3"),
            "--output",
            str(out),
        ],
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert db.total_changes == original_changes
    report = json.loads(out.read_text())
    assert report["summary"]["jobs"]["parked"] == 1
    assert "secret-owner" not in out.read_text()
    assert "PRIVATE_CANONICAL_SCENE_STORY" not in out.read_text()


def test_sqlite_missing_file_cannot_be_created(tmp_path):
    path = tmp_path / "missing.sqlite3"
    proc = subprocess.run(
        [sys.executable, "tools/inspect_summary_recovery.py", "--database", str(path)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert proc.returncode != 0
    assert not path.exists()


def test_catchup_complete_requires_valid_accepted_prefix_and_finished_job(db):
    add_session(db, "finished")
    assert summary_recovery_snapshot(db, now=200000)["summary"]["catchup_complete"] is False
    last = db.execute("SELECT max(id) FROM messages WHERE chat_id='finished'").fetchone()[0]
    db.execute("UPDATE memory_layer_state SET covered_id=? WHERE chat_id='finished' AND layer='summary'", (last,))
    db.execute("UPDATE memory_jobs SET completed_version=dirty_version WHERE chat_id='finished' AND layer='summary'")
    db.commit()
    result = summary_recovery_snapshot(db, now=200000)
    assert result["summary"]["catchup_complete"] is True
    assert result["approval_ready"] is False  # Causal/reader quality is a distinct gate.
    db.execute("UPDATE memory_layer_state SET invalidated_from_id=1 WHERE chat_id='finished' AND layer='summary'")
    db.commit()
    assert summary_recovery_snapshot(db, now=200000)["summary"]["catchup_complete"] is False


def test_diagnostic_errors_allowlist_never_serializes_unknown_provider_text(db):
    add_session(db, "private-source")
    db.execute("UPDATE memory_jobs SET attempts=14,last_error='SECRET_PROVIDER_CANARY' WHERE layer='summary'")
    db.commit()
    result = summary_recovery_snapshot(db, now=200000)
    assert result["summary"]["jobs"]["max_attempts"] == 14
    assert result["summary"]["jobs"]["error_codes"] == {"other": 1}
    assert "SECRET_PROVIDER_CANARY" not in json.dumps(result)
