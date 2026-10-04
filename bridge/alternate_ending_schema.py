"""Permanent branch provenance; transient work continues to use existing operations."""

import sqlite3


def migrate_alternate_ending_lineage(db: sqlite3.Connection) -> None:
    if not db.in_transaction:
        raise RuntimeError("Alternate-ending migration requires an active transaction")
    db.execute(
        "CREATE TABLE narrative_branches("
        "chat_id TEXT NOT NULL,target_session_id TEXT NOT NULL,origin_session_id TEXT NOT NULL,"
        "checkpoint_id TEXT NOT NULL,checkpoint_revision INTEGER NOT NULL,operation_id TEXT NOT NULL UNIQUE,"
        "memory_status TEXT NOT NULL DEFAULT 'pending' "
        "CHECK(memory_status IN ('pending','disabled','ready','degraded')),"
        "created_at REAL NOT NULL,PRIMARY KEY(chat_id,target_session_id),"
        "FOREIGN KEY(chat_id,target_session_id) REFERENCES sessions(chat_id,session_id) ON DELETE CASCADE)"
    )
    # The original may be deleted later; no source-session FK may delete the independent branch.
    db.execute("CREATE INDEX narrative_branches_origin_idx ON narrative_branches(chat_id,origin_session_id)")
    db.execute(
        "CREATE TRIGGER narrative_branch_identity BEFORE UPDATE OF chat_id,target_session_id,origin_session_id,"
        "checkpoint_id,checkpoint_revision,operation_id ON narrative_branches "
        "BEGIN SELECT RAISE(ABORT,'Alternate-ending provenance is immutable'); END"
    )
