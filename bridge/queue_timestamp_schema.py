"""Record real pending-job origins without manufacturing historical enqueue ages."""

import sqlite3


def migrate_queue_timestamps(db: sqlite3.Connection) -> None:
    db.execute("ALTER TABLE memory_jobs ADD COLUMN pending_since REAL")
    # Existing pending rows deliberately stay NULL. Triggers cover every actual
    # enqueue path, including transcript mutations and explicit layer replay.
    db.execute("""CREATE TRIGGER memory_pending_insert AFTER INSERT ON memory_jobs
        WHEN NEW.dirty_version>NEW.completed_version BEGIN
        UPDATE memory_jobs SET pending_since=(julianday('now')-2440587.5)*86400.0 WHERE rowid=NEW.rowid; END""")
    db.execute("""CREATE TRIGGER memory_pending_dirty AFTER UPDATE OF dirty_version ON memory_jobs
        WHEN NEW.dirty_version>OLD.dirty_version AND NEW.dirty_version>NEW.completed_version BEGIN
        UPDATE memory_jobs SET pending_since=CASE
          WHEN OLD.completed_version>=OLD.dirty_version
          THEN (julianday('now')-2440587.5)*86400.0 ELSE OLD.pending_since END
        WHERE rowid=NEW.rowid; END""")
