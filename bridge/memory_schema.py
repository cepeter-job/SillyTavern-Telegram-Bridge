"""Forward migration for durable, independent story-memory coverage."""

import hashlib
import re
import sqlite3

LAYERS = ("hindsight", "episodes", "summary", "scene", "npc", "curator")


def _enqueue_sql(owner: str) -> str:
    return "".join(
        f"""INSERT INTO memory_jobs(chat_id,session_id,session_created_at,layer,target_id)
        SELECT s.chat_id,s.session_id,s.created_at,'{layer}',
        COALESCE((SELECT MAX(id) FROM messages WHERE chat_id=s.chat_id AND session_id=s.session_id),0)
        FROM sessions s WHERE s.chat_id={owner}.chat_id AND s.session_id={owner}.session_id
        ON CONFLICT(chat_id,session_id,session_created_at,layer) DO UPDATE SET
        dirty_version=memory_jobs.dirty_version+1,target_id=excluded.target_id,
        attempts=0,last_error='',next_attempt_at=0;"""  # noqa: S608 -- fixed internal trigger identifiers
        for layer in LAYERS
    )


def _invalidate_sql(owner: str) -> str:
    return f"""  -- internal fixed trigger identifiers
    UPDATE memory_segments SET valid=0 WHERE chat_id={owner}.chat_id
      AND session_id={owner}.session_id AND end_id>={owner}.id;
    UPDATE memory_layer_state SET rewrite_identity=rewrite_identity+1,
      invalidated_from_id=CASE WHEN invalidated_from_id IS NULL THEN {owner}.id
        ELSE MIN(invalidated_from_id,{owner}.id) END,
      covered_id=CASE WHEN layer IN ('hindsight','episodes') THEN MAX(source_floor_id,MIN(covered_id,{owner}.id-1))
        ELSE 0 END
      WHERE chat_id={owner}.chat_id AND session_id={owner}.session_id;
    DELETE FROM session_summaries WHERE chat_id={owner}.chat_id AND session_id={owner}.session_id;
    DELETE FROM episodic_memories WHERE chat_id={owner}.chat_id AND session_id={owner}.session_id
      AND source_end_rowid>={owner}.id;
    DELETE FROM scene_states WHERE chat_id={owner}.chat_id AND session_id={owner}.session_id;
    UPDATE npc_extraction_state SET updated_through_rowid=MIN(updated_through_rowid,{owner}.id-1)
      WHERE chat_id={owner}.chat_id AND session_id={owner}.session_id;
    DELETE FROM meta WHERE key='memory_curator:' || {owner}.chat_id || ':' || {owner}.session_id;
    """  # noqa: S608 -- fixed internal trigger identifiers


def migrate_durable_memory(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE memory_jobs(
        chat_id TEXT NOT NULL,session_id TEXT NOT NULL,session_created_at REAL NOT NULL,layer TEXT NOT NULL,
        dirty_version INTEGER NOT NULL DEFAULT 1,completed_version INTEGER NOT NULL DEFAULT 0,
        target_id INTEGER NOT NULL DEFAULT 0,claimed_version INTEGER NOT NULL DEFAULT 0,
        claimed_target_id INTEGER NOT NULL DEFAULT 0,lease_token TEXT NOT NULL DEFAULT '',
        lease_deadline REAL NOT NULL DEFAULT 0,attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt_at REAL NOT NULL DEFAULT 0,last_error TEXT NOT NULL DEFAULT '',
        PRIMARY KEY(chat_id,session_id,session_created_at,layer))""")
    db.execute("""CREATE TABLE memory_layer_state(
        chat_id TEXT NOT NULL,session_id TEXT NOT NULL,session_created_at REAL NOT NULL,layer TEXT NOT NULL,
        covered_id INTEGER NOT NULL DEFAULT 0,rewrite_identity INTEGER NOT NULL DEFAULT 0,
        purge_epoch INTEGER NOT NULL DEFAULT 0,source_floor_id INTEGER NOT NULL DEFAULT 0,
        invalidated_from_id INTEGER,
        PRIMARY KEY(chat_id,session_id,session_created_at,layer))""")
    db.execute("""CREATE TABLE memory_segments(
        document_id TEXT PRIMARY KEY,chat_id TEXT NOT NULL,session_id TEXT NOT NULL,
        session_created_at REAL NOT NULL,layer TEXT NOT NULL,start_id INTEGER NOT NULL,end_id INTEGER NOT NULL,
        start_offset INTEGER NOT NULL,end_offset INTEGER NOT NULL,source_digest TEXT NOT NULL,
        rewrite_identity INTEGER NOT NULL,purge_epoch INTEGER NOT NULL,valid INTEGER NOT NULL DEFAULT 1,
        created_at REAL NOT NULL)""")
    db.execute("""CREATE TABLE memory_retired_documents(
        chat_id TEXT NOT NULL,session_id TEXT NOT NULL,document_id TEXT NOT NULL,
        deleted INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(chat_id,session_id,document_id))""")
    db.execute(
        "INSERT INTO memory_retired_documents(chat_id,session_id,document_id) "
        "SELECT chat_id,session_id,document_id FROM hindsight_documents WHERE kind IN ('conversation','curated')"
    )
    for chat_id, session_id in db.execute("SELECT chat_id,session_id FROM sessions").fetchall():
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", session_id).strip("-.")[:80] or "session"
        digest = hashlib.sha256(session_id.encode()).hexdigest()[:12]
        prefix = f"st-session-{safe}-{digest}"
        for document_id in (prefix + "-conversation", prefix + "-curated", f"st-session-{session_id}"):
            db.execute(
                "INSERT OR IGNORE INTO memory_retired_documents(chat_id,session_id,document_id) VALUES(?,?,?)",
                (chat_id, session_id, document_id),
            )
    db.execute("""CREATE TRIGGER memory_source_retired AFTER UPDATE OF valid ON memory_segments
        WHEN NEW.valid=0 AND OLD.layer='hindsight' BEGIN
        INSERT INTO memory_retired_documents(chat_id,session_id,document_id)
        VALUES(OLD.chat_id,OLD.session_id,OLD.document_id)
        ON CONFLICT(chat_id,session_id,document_id) DO UPDATE SET deleted=0; END""")
    db.execute("CREATE INDEX memory_jobs_pending_idx ON memory_jobs(next_attempt_at,lease_deadline)")
    db.execute("CREATE INDEX memory_segments_source_idx ON memory_segments(chat_id,session_id,layer,end_id)")
    # Every durable job owns its independent progress identity.
    db.execute("""CREATE TRIGGER memory_job_state AFTER INSERT ON memory_jobs BEGIN
        INSERT OR IGNORE INTO memory_layer_state(chat_id,session_id,session_created_at,layer)
        VALUES(NEW.chat_id,NEW.session_id,NEW.session_created_at,NEW.layer); END""")
    db.execute(f"""CREATE TRIGGER memory_message_insert AFTER INSERT ON messages BEGIN
        {_enqueue_sql("NEW")} END""")
    db.execute(f"""CREATE TRIGGER memory_message_delete AFTER DELETE ON messages BEGIN
        {_invalidate_sql("OLD")} {_enqueue_sql("OLD")} END""")
    db.execute(f"""CREATE TRIGGER memory_message_update
        AFTER UPDATE OF content,role,chat_id,session_id ON messages
        WHEN OLD.content IS NOT NEW.content OR OLD.role IS NOT NEW.role
          OR OLD.chat_id IS NOT NEW.chat_id OR OLD.session_id IS NOT NEW.session_id
        BEGIN {_invalidate_sql("OLD")} {_enqueue_sql("OLD")}
          {_invalidate_sql("NEW")}
          {_enqueue_sql("NEW")}
        END""")
    db.execute("""CREATE TRIGGER memory_session_delete AFTER DELETE ON sessions BEGIN
        INSERT INTO memory_retired_documents(chat_id,session_id,document_id)
        SELECT chat_id,session_id,document_id FROM hindsight_documents
        WHERE chat_id=OLD.chat_id AND session_id=OLD.session_id
        ON CONFLICT(chat_id,session_id,document_id) DO UPDATE SET deleted=0;
        DELETE FROM hindsight_documents WHERE chat_id=OLD.chat_id AND session_id=OLD.session_id;
        DELETE FROM memory_jobs WHERE chat_id=OLD.chat_id AND session_id=OLD.session_id;
        DELETE FROM memory_layer_state WHERE chat_id=OLD.chat_id AND session_id=OLD.session_id;
        UPDATE memory_segments SET valid=0 WHERE chat_id=OLD.chat_id AND session_id=OLD.session_id;
        END""")
    for layer in LAYERS:
        db.execute(
            "INSERT INTO memory_jobs(chat_id,session_id,session_created_at,layer,target_id) "
            "SELECT s.chat_id,s.session_id,s.created_at,?,MAX(m.id) FROM sessions s JOIN messages m "
            "ON m.chat_id=s.chat_id AND m.session_id=s.session_id GROUP BY s.chat_id,s.session_id",
            (layer,),
        )
    # Existing installations may already have explicitly purged external memory.
    # No historical floor was recorded; conservatively keep their current rows local.
    for chat_id, session_id, raw_epoch in db.execute(
        "SELECT s.chat_id,s.session_id,m.value FROM sessions s JOIN meta m "
        "ON m.key='hindsight_epoch:' || s.chat_id || ':' || s.session_id"
    ).fetchall():
        try:
            epoch = max(0, int(raw_epoch))
        except (TypeError, ValueError):
            continue
        if epoch:
            floor = db.execute(
                "SELECT COALESCE(MAX(id),0) FROM messages WHERE chat_id=? AND session_id=?", (chat_id, session_id)
            ).fetchone()[0]
            db.execute(
                "UPDATE memory_layer_state SET purge_epoch=?,source_floor_id=?,covered_id=? "
                "WHERE chat_id=? AND session_id=? AND layer IN ('hindsight','curator')",
                (epoch, floor, floor, chat_id, session_id),
            )
            db.execute(
                "UPDATE memory_jobs SET completed_version=dirty_version WHERE chat_id=? AND session_id=? "
                "AND layer IN ('hindsight','curator')",
                (chat_id, session_id),
            )
