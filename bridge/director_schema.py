"""Forward-only Director lease and history-edit clocks; no model work at startup."""

import sqlite3

from bridge.narrative_runtime_schema import _advance_clock

_ADDITIONS = (
    "ALTER TABLE narrative_state ADD COLUMN rewrite_revision INTEGER NOT NULL DEFAULT 0 CHECK(rewrite_revision >= 0)",
    "ALTER TABLE director_state ADD COLUMN active_proposal_json TEXT NOT NULL DEFAULT '{}' "
    "CHECK(json_valid(active_proposal_json) AND length(active_proposal_json) <= 16384)",
    "ALTER TABLE director_state ADD COLUMN accepted_rewrite_revision INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE director_state ADD COLUMN accepted_settings_revision INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE director_state ADD COLUMN accepted_scene_id TEXT NOT NULL DEFAULT '' "
    "CHECK(length(accepted_scene_id) <= 100)",
    "ALTER TABLE director_state ADD COLUMN direction_source TEXT NOT NULL DEFAULT 'ai' "
    "CHECK(direction_source IN ('ai','user'))",
    "ALTER TABLE director_state ADD COLUMN direction_until_turn INTEGER NOT NULL DEFAULT 0 "
    "CHECK(direction_until_turn >= 0)",
    "ALTER TABLE director_state ADD COLUMN inflight_token TEXT NOT NULL DEFAULT '' "
    "CHECK(length(inflight_token) <= 100)",
    "ALTER TABLE director_state ADD COLUMN inflight_started_at REAL NOT NULL DEFAULT 0",
    "ALTER TABLE director_state ADD COLUMN last_attempt_at REAL NOT NULL DEFAULT 0",
    "ALTER TABLE director_state ADD COLUMN last_attempt_turn INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE director_state ADD COLUMN last_event_key TEXT NOT NULL DEFAULT '' "
    "CHECK(length(last_event_key) <= 500)",
)


def migrate_director_runtime(db: sqlite3.Connection) -> None:
    if not db.in_transaction:
        raise RuntimeError("Director migration requires an active transaction")
    for statement in _ADDITIONS:
        db.execute(statement)
    for name in (
        "narrative_message_insert",
        "narrative_message_delete",
        "narrative_message_edit",
        "narrative_message_move",
    ):
        db.execute(f"DROP TRIGGER {name}")
    db.execute(
        "CREATE TRIGGER narrative_message_insert AFTER INSERT ON messages BEGIN "  # noqa: S608 -- fixed DDL, no external values
        + _advance_clock("NEW", invalidate=False)
        + "UPDATE narrative_state SET rewrite_revision=rewrite_revision+1 "
        "WHERE chat_id=NEW.chat_id AND session_id=NEW.session_id AND NEW.id<=updated_through_rowid; END"
    )
    db.execute(
        "CREATE TRIGGER narrative_message_delete AFTER DELETE ON messages BEGIN "  # noqa: S608 -- fixed DDL, no external values
        + _advance_clock("OLD", invalidate=True)
        + "UPDATE narrative_state SET rewrite_revision=rewrite_revision+1 "
        "WHERE chat_id=OLD.chat_id AND session_id=OLD.session_id; END"
    )
    db.execute(
        "CREATE TRIGGER narrative_message_edit AFTER UPDATE OF content,role,chat_id,session_id ON messages "  # noqa: S608 -- fixed DDL, no external values
        "WHEN NEW.content IS NOT OLD.content OR NEW.role IS NOT OLD.role OR NEW.chat_id IS NOT OLD.chat_id "
        "OR NEW.session_id IS NOT OLD.session_id BEGIN "
        + _advance_clock("OLD", invalidate=True)
        + "UPDATE narrative_state SET rewrite_revision=rewrite_revision+1 "
        "WHERE chat_id=OLD.chat_id AND session_id=OLD.session_id; END"
    )
    db.execute(
        "CREATE TRIGGER narrative_message_move AFTER UPDATE OF chat_id,session_id ON messages "  # noqa: S608 -- fixed DDL, no external values
        "WHEN NEW.chat_id IS NOT OLD.chat_id OR NEW.session_id IS NOT OLD.session_id BEGIN "
        + _advance_clock("NEW", invalidate=True)
        + "UPDATE narrative_state SET rewrite_revision=rewrite_revision+1 "
        "WHERE chat_id=NEW.chat_id AND session_id=NEW.session_id; END"
    )
