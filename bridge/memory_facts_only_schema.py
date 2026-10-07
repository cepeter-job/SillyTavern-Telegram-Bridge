"""One-time retirement of raw Hindsight work, preserving native generations and debt."""

import sqlite3


def migrate_facts_only_hindsight(db: sqlite3.Connection) -> None:
    targets = db.execute(
        "SELECT chat_id,session_id,document_id FROM memory_segments WHERE layer='hindsight' UNION "
        "SELECT chat_id,session_id,document_id FROM hindsight_documents WHERE kind='source_segment' UNION "
        "SELECT chat_id,session_id,document_id FROM memory_archival_attempts WHERE kind='raw'"
    ).fetchall()
    for chat_id, session_id, document_id in targets:
        db.execute(
            "INSERT INTO memory_retired_documents(chat_id,session_id,document_id) VALUES(?,?,?) "
            "ON CONFLICT(chat_id,session_id,document_id) DO UPDATE SET deleted=0,next_attempt_at=0,"
            "retirement_revision=memory_retired_documents.retirement_revision+1",
            (chat_id, session_id, document_id),
        )
    # Targets are durable in this migration transaction. Avoid reopening them twice.
    db.execute("DROP TRIGGER memory_source_retired")
    db.execute("UPDATE memory_segments SET valid=0 WHERE layer='hindsight'")
    db.execute("DELETE FROM hindsight_documents WHERE kind='source_segment'")
    db.execute("""CREATE TRIGGER memory_source_retired AFTER UPDATE OF valid ON memory_segments
        WHEN NEW.valid=0 AND OLD.valid<>0 AND OLD.layer='hindsight' BEGIN
        INSERT INTO memory_retired_documents(chat_id,session_id,document_id)
        VALUES(OLD.chat_id,OLD.session_id,OLD.document_id)
        ON CONFLICT(chat_id,session_id,document_id) DO UPDATE SET deleted=0,next_attempt_at=0,
        retirement_revision=memory_retired_documents.retirement_revision+1; END""")
    # Revoke captured raw claims without invalidating same-epoch native documents.
    db.execute(
        "UPDATE memory_layer_state SET rewrite_identity=rewrite_identity+1,covered_id=source_floor_id "
        "WHERE layer='hindsight'"
    )
    for owner in db.execute(
        "SELECT chat_id,session_id,session_created_at FROM memory_jobs WHERE layer='hindsight'"
    ).fetchall():
        # Queue same-incarnation, same-epoch candidates. The native worker still
        # performs full provenance/payload validation before any external call.
        native_work = db.execute(
            "SELECT 1 FROM memory_fact_index f JOIN sessions s ON s.chat_id=f.chat_id AND "
            "s.session_id=f.session_id AND s.created_at=f.session_created_at JOIN memory_layer_state l "
            "ON l.chat_id=f.chat_id AND l.session_id=f.session_id AND l.session_created_at=f.session_created_at "
            "AND l.layer='hindsight' AND l.purge_epoch=f.external_epoch WHERE f.chat_id=? AND f.session_id=? "
            "AND f.session_created_at=? AND f.state='pending' AND NOT EXISTS(SELECT 1 FROM "
            "memory_retired_documents r WHERE r.document_id=f.document_id) LIMIT 1",
            owner,
        ).fetchone()
        db.execute(
            "UPDATE memory_jobs SET completed_version=dirty_version,dirty_version=dirty_version+?,"
            "lease_token='',lease_deadline=0,claimed_version=0,claimed_target_id=0,attempts=0,next_attempt_at=0,"
            "last_error='' WHERE chat_id=? AND session_id=? AND session_created_at=? AND layer='hindsight'",
            (int(bool(native_work)), *owner),
        )
