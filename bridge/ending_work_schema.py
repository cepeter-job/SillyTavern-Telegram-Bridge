"""Durable ending work identities and committed-finale evidence, without external I/O."""

import sqlite3

_ADDITIONS = (
    "ALTER TABLE ending_state ADD COLUMN resolution_evidence_json TEXT NOT NULL DEFAULT '[]' "
    "CHECK(json_valid(resolution_evidence_json) AND length(resolution_evidence_json)<=4096)",
    "ALTER TABLE ending_state ADD COLUMN epilogue_brief_json TEXT NOT NULL DEFAULT '' "
    "CHECK(length(epilogue_brief_json)<=16000 AND (epilogue_brief_json='' OR json_valid(epilogue_brief_json)))",
    "ALTER TABLE ending_state ADD COLUMN work_token TEXT NOT NULL DEFAULT '' CHECK(length(work_token)<=100)",
    "ALTER TABLE ending_state ADD COLUMN work_started_at REAL NOT NULL DEFAULT 0",
    "ALTER TABLE ending_state ADD COLUMN work_stage TEXT NOT NULL DEFAULT '' "
    "CHECK(work_stage IN ('','brief','story','commit_epilogue','reconcile','delivery'))",
    "ALTER TABLE ending_state ADD COLUMN last_error TEXT NOT NULL DEFAULT '' CHECK(length(last_error)<=300)",
    "ALTER TABLE ending_state ADD COLUMN last_attempt_at REAL NOT NULL DEFAULT 0",
)


def migrate_ending_workflow(db: sqlite3.Connection) -> None:
    if not db.in_transaction:
        raise RuntimeError("Ending workflow migration requires an active transaction")
    for statement in _ADDITIONS:
        db.execute(statement)
    db.execute(
        "CREATE TRIGGER finale_assistant_commit AFTER INSERT ON messages WHEN NEW.role='assistant' "
        "BEGIN UPDATE ending_state SET finale_committed_rowid=NEW.id,lifecycle_revision=lifecycle_revision+1 "
        "WHERE chat_id=NEW.chat_id AND session_id=NEW.session_id AND lifecycle='finale'; END"
    )
    db.execute(
        "CREATE TRIGGER finale_assistant_edit AFTER UPDATE OF content,role ON messages "
        "WHEN (NEW.content IS NOT OLD.content OR NEW.role IS NOT OLD.role) "
        "AND (NEW.role='assistant' OR OLD.role='assistant') "
        "BEGIN UPDATE ending_state SET lifecycle_revision=lifecycle_revision+1,finale_committed_rowid=("
        "SELECT MAX(m.id) FROM messages m JOIN narrative_checkpoints c ON c.checkpoint_id=ending_state.checkpoint_id "
        "WHERE m.chat_id=ending_state.chat_id AND m.session_id=ending_state.session_id "
        "AND m.role='assistant' AND m.id>c.through_rowid) "
        "WHERE chat_id=OLD.chat_id AND session_id=OLD.session_id AND lifecycle='finale'; END"
    )
    db.execute(
        "CREATE TRIGGER finale_assistant_delete AFTER DELETE ON messages WHEN OLD.role='assistant' "
        "BEGIN UPDATE ending_state SET lifecycle_revision=lifecycle_revision+1,finale_committed_rowid=("
        "SELECT MAX(m.id) FROM messages m JOIN narrative_checkpoints c ON c.checkpoint_id=ending_state.checkpoint_id "
        "WHERE m.chat_id=ending_state.chat_id AND m.session_id=ending_state.session_id "
        "AND m.role='assistant' AND m.id>c.through_rowid) "
        "WHERE chat_id=OLD.chat_id AND session_id=OLD.session_id AND lifecycle='finale'; END"
    )
    db.execute("CREATE INDEX ending_workflow_pending_idx ON ending_state(lifecycle,last_attempt_at,chat_id,session_id)")
