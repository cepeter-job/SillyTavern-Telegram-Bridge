"""Session-owned preflight receipts, invalidated at transcript rewrite boundaries."""

import sqlite3


def migrate_action_adjudication(db: sqlite3.Connection) -> None:
    if not db.in_transaction:
        raise RuntimeError("Action migration requires a transaction")
    db.execute("""CREATE TABLE action_attempts(
        chat_id TEXT NOT NULL,session_id TEXT NOT NULL,request_key TEXT NOT NULL,
        session_created_at REAL NOT NULL,epoch INTEGER NOT NULL,actor_id TEXT NOT NULL,
        action TEXT NOT NULL,action_digest TEXT NOT NULL,snapshot TEXT NOT NULL,
        through_rowid INTEGER NOT NULL,status TEXT NOT NULL CHECK(status IN ('pending','ready','bound')),
        lease_token TEXT NOT NULL,lease_until REAL NOT NULL,result_json TEXT NOT NULL DEFAULT '{}'
            CHECK(json_valid(result_json) AND length(result_json)<=16384),
        source_rowid INTEGER,created_at REAL NOT NULL,
        PRIMARY KEY(chat_id,session_id,request_key),
        FOREIGN KEY(chat_id,session_id) REFERENCES sessions(chat_id,session_id) ON DELETE CASCADE,
        FOREIGN KEY(source_rowid) REFERENCES messages(id) ON DELETE CASCADE)""")
    db.execute("CREATE INDEX action_attempt_source_idx ON action_attempts(chat_id,session_id,source_rowid)")
    # Pending edits use the preceding prefix and survive their own accepted rewrite.
    for event, condition in (
        ("DELETE", ""),
        (
            "UPDATE OF content,role,chat_id,session_id",
            "WHEN NEW.content IS NOT OLD.content OR NEW.role IS NOT OLD.role "
            "OR NEW.chat_id IS NOT OLD.chat_id OR NEW.session_id IS NOT OLD.session_id",
        ),
    ):
        name = "delete" if event == "DELETE" else "edit"
        db.execute(
            f"CREATE TRIGGER action_message_{name} AFTER {event} ON messages {condition} BEGIN "  # noqa: S608
            "DELETE FROM action_attempts WHERE chat_id=OLD.chat_id AND session_id=OLD.session_id "
            "AND (source_rowid>=OLD.id OR (source_rowid IS NULL AND through_rowid>=OLD.id)); END"
        )
