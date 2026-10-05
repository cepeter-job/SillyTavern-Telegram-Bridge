"""Forward migration for temporal fact attestations and artifact classification."""

import sqlite3

from bridge.memory_schema import _enqueue_sql, _invalidate_sql


def migrate_memory_knowledge(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE memory_source_rewrites(
        change_id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id TEXT NOT NULL,session_id TEXT NOT NULL,
        session_created_at REAL NOT NULL,source_rowid INTEGER NOT NULL)""")
    db.execute("""CREATE INDEX memory_source_rewrite_scope_idx
        ON memory_source_rewrites(chat_id,session_id,session_created_at,change_id)""")
    db.execute("""CREATE TABLE memory_explicit_events(
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id TEXT NOT NULL,session_id TEXT NOT NULL,
        session_created_at REAL NOT NULL,principal TEXT NOT NULL,fact TEXT NOT NULL,
        accepted_after_rowid INTEGER NOT NULL,external_epoch INTEGER NOT NULL,
        event_key TEXT NOT NULL UNIQUE,valid INTEGER NOT NULL DEFAULT 1,accepted_at REAL NOT NULL)""")
    db.execute("""CREATE TABLE memory_fact_provenance(
        memory_id INTEGER PRIMARY KEY,chat_id TEXT NOT NULL,session_id TEXT NOT NULL,
        session_created_at REAL NOT NULL,provenance_kind TEXT NOT NULL,
        source_document_id TEXT,explicit_event_id INTEGER,attestation_key TEXT NOT NULL UNIQUE,
        payload_digest TEXT NOT NULL,valid INTEGER NOT NULL DEFAULT 1)""")
    db.execute("""CREATE TABLE memory_fact_index(
        document_id TEXT PRIMARY KEY,memory_id INTEGER NOT NULL,chat_id TEXT NOT NULL,session_id TEXT NOT NULL,
        session_created_at REAL NOT NULL,external_epoch INTEGER NOT NULL,payload_digest TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'pending',last_error TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL)""")
    db.execute("""CREATE TABLE memory_artifact_visibility(
        chat_id TEXT NOT NULL,session_id TEXT NOT NULL,session_created_at REAL NOT NULL,
        artifact_kind TEXT NOT NULL,through_rowid INTEGER NOT NULL,payload_digest TEXT NOT NULL,
        rewrite_revision INTEGER NOT NULL,schema_version INTEGER NOT NULL,blocks_json TEXT NOT NULL,
        PRIMARY KEY(chat_id,session_id,artifact_kind))""")
    db.execute("CREATE INDEX memory_fact_source_idx ON memory_fact_provenance(source_document_id,valid)")
    db.execute("CREATE INDEX memory_fact_scope_idx ON memory_fact_provenance(chat_id,session_id,session_created_at)")
    db.execute("CREATE INDEX memory_fact_pending_idx ON memory_fact_index(chat_id,session_id,state,created_at)")
    db.execute("CREATE INDEX memory_explicit_scope_idx ON memory_explicit_events(chat_id,session_id,event_id)")
    for definition in (
        "attempts INTEGER NOT NULL DEFAULT 0",
        "next_attempt_at REAL NOT NULL DEFAULT 0",
        "lease_token TEXT NOT NULL DEFAULT ''",
        "lease_deadline REAL NOT NULL DEFAULT 0",
    ):
        db.execute("ALTER TABLE memory_retired_documents ADD COLUMN " + definition)
    db.execute("""CREATE TRIGGER memory_fact_index_retired AFTER UPDATE OF state ON memory_fact_index
        WHEN NEW.state='retired' AND OLD.state<>'retired' BEGIN
        INSERT INTO memory_retired_documents(chat_id,session_id,document_id)
        VALUES(OLD.chat_id,OLD.session_id,OLD.document_id)
        ON CONFLICT(chat_id,session_id,document_id) DO UPDATE SET deleted=0,next_attempt_at=0; END""")
    db.execute("""CREATE TRIGGER memory_fact_invalid AFTER UPDATE OF valid ON memory_fact_provenance
        WHEN NEW.valid=0 BEGIN UPDATE memory_fact_index SET state='retired'
        WHERE memory_id=OLD.memory_id; END""")
    db.execute("""CREATE TRIGGER memory_fact_deleted AFTER DELETE ON episodic_memories BEGIN
        UPDATE memory_fact_index SET state='retired' WHERE memory_id=OLD.memory_id;
        DELETE FROM memory_fact_provenance WHERE memory_id=OLD.memory_id;
        DELETE FROM episodic_memory_visibility WHERE memory_id=OLD.memory_id; END""")
    db.execute("""CREATE TRIGGER memory_fact_changed AFTER UPDATE ON episodic_memories BEGIN
        UPDATE memory_fact_provenance SET valid=0 WHERE memory_id=OLD.memory_id; END""")
    for operation in ("UPDATE", "DELETE"):
        db.execute(
            f"""CREATE TRIGGER memory_fact_audience_{operation.lower()}
            AFTER {operation} ON episodic_memory_visibility BEGIN
            UPDATE memory_fact_provenance SET valid=0 WHERE memory_id=OLD.memory_id; END"""  # noqa: S608 -- fixed migration literals
        )
    db.execute("""CREATE TRIGGER memory_fact_source_invalid AFTER UPDATE OF valid ON memory_segments
        WHEN NEW.valid=0 BEGIN UPDATE memory_fact_provenance SET valid=0
        WHERE source_document_id=OLD.document_id; END""")
    db.execute("""CREATE TRIGGER memory_fact_source_deleted AFTER DELETE ON memory_segments BEGIN
        UPDATE memory_fact_provenance SET valid=0 WHERE source_document_id=OLD.document_id; END""")
    db.execute("""CREATE TRIGGER memory_explicit_invalid AFTER UPDATE OF valid ON memory_explicit_events
        WHEN NEW.valid=0 BEGIN UPDATE memory_fact_provenance SET valid=0
        WHERE explicit_event_id=OLD.event_id; END""")
    for operation in ("DELETE", "UPDATE OF content,role,chat_id,session_id"):
        suffix = "delete" if operation == "DELETE" else "update"
        condition = (
            ""
            if suffix == "delete"
            else (
                "WHEN OLD.content IS NOT NEW.content OR OLD.role IS NOT NEW.role "
                "OR OLD.chat_id IS NOT NEW.chat_id OR OLD.session_id IS NOT NEW.session_id"
            )
        )
        db.execute(
            f"""CREATE TRIGGER memory_explicit_prefix_{suffix} AFTER {operation} ON messages
            {condition} BEGIN UPDATE memory_explicit_events SET valid=0 WHERE chat_id=OLD.chat_id
            AND session_id=OLD.session_id AND accepted_after_rowid>=OLD.id; END"""  # noqa: S608 -- fixed migration literals
        )
    db.execute("""CREATE TRIGGER memory_knowledge_session_deleted AFTER DELETE ON sessions BEGIN
        UPDATE memory_fact_provenance SET valid=0 WHERE chat_id=OLD.chat_id AND session_id=OLD.session_id;
        UPDATE memory_explicit_events SET valid=0 WHERE chat_id=OLD.chat_id AND session_id=OLD.session_id;
        DELETE FROM memory_artifact_visibility WHERE chat_id=OLD.chat_id AND session_id=OLD.session_id;
        DELETE FROM memory_source_rewrites WHERE chat_id=OLD.chat_id AND session_id=OLD.session_id; END""")
    db.execute("""CREATE TRIGGER memory_fact_external_purge AFTER UPDATE OF purge_epoch ON memory_layer_state
        WHEN NEW.layer='hindsight' AND OLD.purge_epoch<>NEW.purge_epoch BEGIN
        UPDATE memory_fact_index SET state='retired' WHERE chat_id=NEW.chat_id AND session_id=NEW.session_id
        AND (external_epoch<>NEW.purge_epoch OR session_created_at<>NEW.session_created_at); END""")
    for table, kind in (("session_summaries", "summary"), ("scene_states", "scene")):
        for operation in ("INSERT", "UPDATE", "DELETE"):
            owner = "OLD" if operation == "DELETE" else "NEW"
            db.execute(
                f"""CREATE TRIGGER memory_{kind}_sidecar_{operation.lower()} AFTER {operation} ON {table}
                BEGIN DELETE FROM memory_artifact_visibility WHERE chat_id={owner}.chat_id
                AND session_id={owner}.session_id AND artifact_kind='{kind}'; END"""  # noqa: S608 -- fixed migration literals
            )
    # Preserve opaque legacy panel data; rebuild authoritative native attestations in existing jobs.
    db.execute("UPDATE memory_segments SET valid=0 WHERE layer='episodes'")
    db.execute("UPDATE memory_layer_state SET covered_id=0 WHERE layer IN ('episodes','summary','scene')")
    db.execute("""UPDATE memory_jobs SET dirty_version=dirty_version+1,next_attempt_at=0
        WHERE layer IN ('episodes','summary','scene')""")
    _repair_memory_mutation_triggers(db)


def _scoped_invalidation_sql(owner: str) -> str:
    """Migration21 refines parent coverage while retaining migration20 source invalidation."""
    sql = _invalidate_sql(owner)
    for table, coverage in (("session_summaries", "covered_until_rowid"), ("scene_states", "updated_through_rowid")):
        original = f"DELETE FROM {table} WHERE chat_id={owner}.chat_id AND session_id={owner}.session_id;"  # noqa: S608 -- fixed migration identifiers
        if sql.count(original) != 1:
            raise RuntimeError("The reviewed migration20 artifact invalidation contract changed")
        sql = sql.replace(original, original[:-1] + f" AND {coverage}>={owner}.id;")
    sql = sql.replace("layer IN ('hindsight','episodes')", "layer IN ('hindsight','episodes','summary','scene')")
    rewrite_sql = f"""
        INSERT INTO memory_source_rewrites(chat_id,session_id,session_created_at,source_rowid)
        SELECT chat_id,session_id,created_at,{owner}.id FROM sessions
        WHERE chat_id={owner}.chat_id AND session_id={owner}.session_id;
        """  # noqa: S608 -- fixed OLD/NEW migration identifiers
    return sql + rewrite_sql


def _repair_memory_mutation_triggers(db: sqlite3.Connection) -> None:
    db.execute("DROP TRIGGER memory_message_delete")
    db.execute("DROP TRIGGER memory_message_update")
    db.execute(f"""CREATE TRIGGER memory_message_delete AFTER DELETE ON messages BEGIN
        {_scoped_invalidation_sql("OLD")} {_enqueue_sql("OLD")} END""")
    db.execute(f"""CREATE TRIGGER memory_message_update
        AFTER UPDATE OF content,role,chat_id,session_id ON messages
        WHEN OLD.content IS NOT NEW.content OR OLD.role IS NOT NEW.role
          OR OLD.chat_id IS NOT NEW.chat_id OR OLD.session_id IS NOT NEW.session_id
        BEGIN {_scoped_invalidation_sql("OLD")} {_enqueue_sql("OLD")}
          {_scoped_invalidation_sql("NEW")} {_enqueue_sql("NEW")}
        END""")
