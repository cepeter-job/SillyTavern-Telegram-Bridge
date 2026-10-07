"""Durable pre-story reservations; committed checks retain their existing owner."""

import sqlite3


def migrate_action_adjudication(db: sqlite3.Connection) -> None:
    if not db.in_transaction:
        raise RuntimeError("Action adjudication migration requires an active transaction")
    db.execute(
        "CREATE TABLE action_preflights("
        "chat_id TEXT NOT NULL,session_id TEXT NOT NULL,request_key TEXT NOT NULL,"
        "scope_digest TEXT NOT NULL,input_digest TEXT NOT NULL,lease_token TEXT NOT NULL,"
        "lease_until REAL NOT NULL,receipt_json TEXT NOT NULL DEFAULT '' "
        "CHECK(length(CAST(receipt_json AS BLOB))<=12000 AND (receipt_json='' OR json_valid(receipt_json))),"
        "PRIMARY KEY(chat_id,session_id,request_key),"
        "FOREIGN KEY(chat_id,session_id) REFERENCES sessions(chat_id,session_id) ON DELETE CASCADE)"
    )
    db.execute(
        "CREATE TRIGGER action_conversation_reset AFTER UPDATE OF value ON meta "
        "WHEN NEW.key LIKE 'conversation_epoch:%' AND NEW.value IS NOT OLD.value BEGIN "
        "DELETE FROM action_preflights WHERE NEW.key='conversation_epoch:'||chat_id||':'||session_id; END"
    )
    db.execute(
        "CREATE TRIGGER action_conversation_epoch_insert AFTER INSERT ON meta "
        "WHEN NEW.key LIKE 'conversation_epoch:%' BEGIN "
        "DELETE FROM action_preflights WHERE NEW.key='conversation_epoch:'||chat_id||':'||session_id; END"
    )
    db.execute(
        "CREATE TRIGGER action_preferences_delete AFTER DELETE ON sessions BEGIN "
        "DELETE FROM meta WHERE key='action_checks:'||OLD.chat_id||':'||OLD.session_id; END"
    )
