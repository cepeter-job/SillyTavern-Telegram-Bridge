"""SQL-only persistence for bounded Mini App operation outcomes."""

from __future__ import annotations

import json
import sqlite3

from bridge.repository_contracts import require_active_transaction


def initialize(db: sqlite3.Connection) -> None:
    require_active_transaction(db)
    db.execute("""CREATE TABLE IF NOT EXISTS miniapp_jobs (
        id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, request_key TEXT NOT NULL,
        fingerprint TEXT NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL,
        created_at REAL NOT NULL, updated_at REAL NOT NULL,
        result_json TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
        UNIQUE(actor_id,request_key))""")
    db.execute("CREATE INDEX IF NOT EXISTS miniapp_jobs_actor ON miniapp_jobs(actor_id,created_at)")


def by_key(db: sqlite3.Connection, actor: str, key: str):
    return db.execute(
        "SELECT id,fingerprint FROM miniapp_jobs WHERE actor_id=? AND request_key=?", (actor, key)
    ).fetchone()


def active_count(db: sqlite3.Connection, actor: str | None = None) -> int:
    if actor is None:
        return int(db.execute("SELECT COUNT(*) FROM miniapp_jobs WHERE state IN ('queued','running')").fetchone()[0])
    return int(
        db.execute(
            "SELECT COUNT(*) FROM miniapp_jobs WHERE actor_id=? AND state IN ('queued','running')", (actor,)
        ).fetchone()[0]
    )


def create(db: sqlite3.Connection, job_id: str, actor: str, key: str, fingerprint: str, kind: str, now: float) -> None:
    require_active_transaction(db)
    db.execute(
        "INSERT INTO miniapp_jobs(id,actor_id,request_key,fingerprint,kind,state,created_at,updated_at) "
        "VALUES(?,?,?,?,?,'queued',?,?)",
        (job_id, actor, key, fingerprint, kind, now, now),
    )


def update(db: sqlite3.Connection, job_id: str, state: str, now: float, result: str = "{}", error: str = "") -> None:
    require_active_transaction(db)
    db.execute(
        "UPDATE miniapp_jobs SET state=?,updated_at=?,result_json=?,error=? WHERE id=?",
        (state, now, result, error, job_id),
    )


def recover(db: sqlite3.Connection, now: float) -> None:
    require_active_transaction(db)
    db.execute(
        "UPDATE miniapp_jobs SET state='interrupted',error='Bridge restarted; review state before trying again.',"
        "updated_at=? WHERE state IN ('queued','running')",
        (now,),
    )


def prune(db: sqlite3.Connection, actor: str, now: float) -> None:
    require_active_transaction(db)
    db.execute(
        "DELETE FROM miniapp_jobs WHERE actor_id=? AND state NOT IN ('queued','running') "
        "AND (created_at<? OR id IN (SELECT id FROM miniapp_jobs WHERE actor_id=? "
        "ORDER BY created_at DESC LIMIT -1 OFFSET 100))",
        (actor, now - 86400, actor),
    )


def load(db: sqlite3.Connection, actor: str, job_id: str) -> dict | None:
    row = db.execute(
        "SELECT id,kind,state,result_json,error,created_at,updated_at FROM miniapp_jobs WHERE actor_id=? AND id=?",
        (actor, job_id),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "kind": row[1],
        "state": row[2],
        "result": json.loads(row[3]),
        "error": row[4],
        "created_at": row[5],
        "updated_at": row[6],
    }


def recent(db: sqlite3.Connection, actor: str) -> list[dict]:
    rows = db.execute(
        "SELECT id FROM miniapp_jobs WHERE actor_id=? ORDER BY created_at DESC LIMIT 20", (actor,)
    ).fetchall()
    return [result for row in rows if (result := load(db, actor, row[0])) is not None]
