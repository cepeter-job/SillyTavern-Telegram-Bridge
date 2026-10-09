"""Forward-only durable classified Summary window archive."""

from __future__ import annotations

import sqlite3


def migrate_summary_window_archive(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE summary_archive_windows("
        "chat_id TEXT NOT NULL,session_id TEXT NOT NULL,session_created_at REAL NOT NULL,"
        "through_rowid INTEGER NOT NULL,source_document_id TEXT NOT NULL,"
        "payload_digest TEXT NOT NULL,blocks_json TEXT NOT NULL,"
        "PRIMARY KEY(chat_id,session_id,session_created_at,through_rowid))"
    )
    db.execute(
        "CREATE INDEX summary_archive_scope_idx ON summary_archive_windows("
        "chat_id,session_id,session_created_at,through_rowid DESC)"
    )
    # A suffix rewrite invalidates the entire archived window containing it.
    # Keep earlier immutable windows; a full purge always destroys the archive.
    db.execute(
        "CREATE TRIGGER summary_archive_invalidate AFTER UPDATE OF rewrite_identity,purge_epoch "
        "ON memory_layer_state WHEN NEW.layer='summary' AND "
        "(NEW.rewrite_identity<>OLD.rewrite_identity OR NEW.purge_epoch<>OLD.purge_epoch) BEGIN "
        "DELETE FROM summary_archive_windows WHERE chat_id=NEW.chat_id AND session_id=NEW.session_id "
        "AND session_created_at=NEW.session_created_at AND "
        "(NEW.purge_epoch<>OLD.purge_epoch OR through_rowid>=COALESCE(NEW.invalidated_from_id,0)); END"
    )
    db.execute(
        "CREATE TRIGGER summary_archive_deleted AFTER DELETE ON sessions BEGIN "
        "DELETE FROM summary_archive_windows WHERE chat_id=OLD.chat_id AND session_id=OLD.session_id; END"
    )
