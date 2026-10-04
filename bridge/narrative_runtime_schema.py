"""Forward migration for narrative transcript clocks; no runtime/provider imports."""

import sqlite3
from typing import Literal


def _advance_clock(owner: Literal["NEW", "OLD"], *, invalidate: bool) -> str:
    # Identifiers are fixed by the migration, never sourced from application data.
    boundary = (
        f"{owner}.id"
        if invalidate
        else (
            "CASE WHEN NEW.id <= COALESCE((SELECT updated_through_rowid FROM narrative_state "
            "WHERE chat_id=NEW.chat_id AND session_id=NEW.session_id),0) THEN NEW.id ELSE NULL END"
        )
    )
    return f"""
        INSERT INTO narrative_state(chat_id,session_id,state_revision,history_revision,invalidated_from_rowid)
        SELECT {owner}.chat_id,{owner}.session_id,1,1,{boundary}
        WHERE EXISTS(SELECT 1 FROM sessions WHERE chat_id={owner}.chat_id AND session_id={owner}.session_id)
        ON CONFLICT(chat_id,session_id) DO UPDATE SET
            state_revision=narrative_state.state_revision+1,
            history_revision=narrative_state.history_revision+1,
            invalidated_from_rowid=CASE WHEN {boundary} IS NULL THEN narrative_state.invalidated_from_rowid
                WHEN narrative_state.invalidated_from_rowid IS NULL THEN {boundary}
                ELSE MIN(narrative_state.invalidated_from_rowid,{boundary}) END;
    """  # noqa: S608 -- migration-only NEW/OLD literals, never application input


def migrate_narrative_history_revisions(db: sqlite3.Connection) -> None:
    if not db.in_transaction:
        raise RuntimeError("Narrative migration requires an active transaction")
    db.execute(
        "ALTER TABLE narrative_state ADD COLUMN reconciled_history_revision INTEGER NOT NULL DEFAULT 0 "
        "CHECK(reconciled_history_revision >= 0)"
    )
    db.execute(
        "ALTER TABLE narrative_state ADD COLUMN invalidated_from_rowid INTEGER "
        "CHECK(invalidated_from_rowid IS NULL OR invalidated_from_rowid > 0)"
    )
    db.execute("CREATE INDEX messages_narrative_cursor_idx ON messages(chat_id,session_id,id)")
    db.execute(
        "CREATE INDEX narrative_checkpoint_boundary_idx ON "
        "narrative_checkpoints(chat_id,session_id,kind,through_rowid,source_revision)"
    )
    db.execute(
        "CREATE TRIGGER narrative_message_insert AFTER INSERT ON messages BEGIN "
        + _advance_clock("NEW", invalidate=False)
        + " END"
    )
    db.execute(
        "CREATE TRIGGER narrative_message_delete AFTER DELETE ON messages BEGIN "
        + _advance_clock("OLD", invalidate=True)
        + " END"
    )
    db.execute(
        "CREATE TRIGGER narrative_message_edit AFTER UPDATE OF content,role,chat_id,session_id ON messages "
        "WHEN NEW.content IS NOT OLD.content OR NEW.role IS NOT OLD.role OR NEW.chat_id IS NOT OLD.chat_id "
        "OR NEW.session_id IS NOT OLD.session_id BEGIN " + _advance_clock("OLD", invalidate=True) + " END"
    )
    db.execute(
        "CREATE TRIGGER narrative_message_move AFTER UPDATE OF chat_id,session_id ON messages "
        "WHEN NEW.chat_id IS NOT OLD.chat_id OR NEW.session_id IS NOT OLD.session_id BEGIN "
        + _advance_clock("NEW", invalidate=True)
        + " END"
    )
