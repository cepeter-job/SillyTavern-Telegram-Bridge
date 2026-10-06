"""Private resumable derived state, with complete-row prefix checkpoints."""

import sqlite3


def migrate_complete_memory_parts(db: sqlite3.Connection) -> None:
    db.execute("ALTER TABLE memory_layer_state ADD COLUMN draft_json TEXT NOT NULL DEFAULT ''")
    db.execute("ALTER TABLE memory_layer_state ADD COLUMN draft_source_id TEXT NOT NULL DEFAULT ''")
    db.execute(
        "CREATE TABLE memory_layer_checkpoints("
        "chat_id TEXT NOT NULL,session_id TEXT NOT NULL,session_created_at REAL NOT NULL,layer TEXT NOT NULL,"
        "through_id INTEGER NOT NULL,source_document_id TEXT NOT NULL,payload_json TEXT NOT NULL,"
        "PRIMARY KEY(chat_id,session_id,session_created_at,layer,through_id))"
    )
    db.execute(
        "CREATE TRIGGER memory_draft_invalidated AFTER UPDATE OF rewrite_identity,purge_epoch ON memory_layer_state "
        "WHEN NEW.layer IN ('summary','scene','curator','npc') AND "
        "(NEW.rewrite_identity<>OLD.rewrite_identity OR NEW.purge_epoch<>OLD.purge_epoch) BEGIN "
        "DELETE FROM memory_layer_checkpoints WHERE chat_id=NEW.chat_id AND session_id=NEW.session_id "
        "AND session_created_at=NEW.session_created_at AND layer=NEW.layer "
        "AND (NEW.purge_epoch<>OLD.purge_epoch OR through_id>=COALESCE(NEW.invalidated_from_id,0)); "
        "UPDATE memory_layer_state SET draft_json='',draft_source_id='' WHERE chat_id=NEW.chat_id "
        "AND session_id=NEW.session_id AND session_created_at=NEW.session_created_at AND layer=NEW.layer; END"
    )
    db.execute(
        "CREATE TRIGGER memory_checkpoint_session_deleted AFTER DELETE ON sessions BEGIN "
        "DELETE FROM memory_layer_checkpoints WHERE chat_id=OLD.chat_id AND session_id=OLD.session_id; END"
    )
    # Covered legacy windows may have omitted row tails. Preserve panel data but
    # remove its authority and rebuild from canonical parts through existing jobs.
    db.execute("DELETE FROM memory_artifact_visibility WHERE artifact_kind IN ('summary','scene')")
    db.execute("UPDATE memory_segments SET valid=0 WHERE layer IN ('summary','scene','curator','npc')")
    db.execute(
        "UPDATE memory_layer_state SET covered_id=source_floor_id,rewrite_identity=rewrite_identity+1,"
        "invalidated_from_id=source_floor_id+1 WHERE layer IN ('summary','scene','curator','npc')"
    )
    db.execute("UPDATE npc_extraction_state SET updated_through_rowid=0")
    db.execute(
        "UPDATE memory_jobs SET dirty_version=dirty_version+1,next_attempt_at=0 "
        "WHERE layer IN ('summary','scene','curator','npc')"
    )
