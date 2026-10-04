"""Revision-bound readiness and manual arc guidance; no external work in migrations."""

import sqlite3

_ADDITIONS = (
    "ALTER TABLE ending_state ADD COLUMN ready_history_revision INTEGER",
    "ALTER TABLE ending_state ADD COLUMN ready_settings_revision INTEGER",
    "ALTER TABLE ending_state ADD COLUMN ready_rewrite_revision INTEGER",
    "ALTER TABLE ending_state ADD COLUMN ready_through_rowid INTEGER",
    "ALTER TABLE ending_state ADD COLUMN readiness_reason TEXT NOT NULL DEFAULT '' "
    "CHECK(length(readiness_reason)<=1000)",
    "ALTER TABLE ending_state ADD COLUMN required_arcs_json TEXT NOT NULL DEFAULT '[]' "
    "CHECK(json_valid(required_arcs_json) AND length(required_arcs_json)<=16384)",
    "ALTER TABLE director_state ADD COLUMN arc_guidance_json TEXT NOT NULL DEFAULT '{}' "
    "CHECK(json_valid(arc_guidance_json) AND length(arc_guidance_json)<=32768)",
)


def _invalidate(owner: str) -> str:
    if owner not in {"OLD", "NEW"}:
        raise ValueError("Invalid readiness trigger owner")
    return (
        "UPDATE ending_state SET lifecycle='open',lifecycle_revision=lifecycle_revision+1,"  # noqa: S608 -- fixed OLD/NEW aliases
        "finale_ready_revision=NULL,ready_history_revision=NULL,ready_settings_revision=NULL,"
        "ready_rewrite_revision=NULL,ready_through_rowid=NULL,readiness_reason='',required_arcs_json='[]' "
        f"WHERE chat_id={owner}.chat_id AND session_id={owner}.session_id AND lifecycle='finale_ready';"
    )


def migrate_ending_readiness(db: sqlite3.Connection) -> None:
    if not db.in_transaction:
        raise RuntimeError("Ending readiness migration requires an active transaction")
    for statement in _ADDITIONS:
        db.execute(statement)
    db.execute(
        "CREATE TRIGGER ending_readiness_message_insert AFTER INSERT ON messages BEGIN " + _invalidate("NEW") + " END"
    )
    db.execute(
        "CREATE TRIGGER ending_readiness_message_delete AFTER DELETE ON messages BEGIN " + _invalidate("OLD") + " END"
    )
    db.execute(
        "CREATE TRIGGER ending_readiness_message_edit AFTER UPDATE OF content,role,chat_id,session_id ON messages "
        "WHEN NEW.content IS NOT OLD.content OR NEW.role IS NOT OLD.role OR NEW.chat_id IS NOT OLD.chat_id "
        "OR NEW.session_id IS NOT OLD.session_id BEGIN " + _invalidate("OLD") + _invalidate("NEW") + " END"
    )
    db.execute(
        "CREATE TRIGGER ending_readiness_settings AFTER UPDATE OF settings_json ON narrative_settings "
        "WHEN NEW.settings_json IS NOT OLD.settings_json BEGIN " + _invalidate("NEW") + " END"
    )
