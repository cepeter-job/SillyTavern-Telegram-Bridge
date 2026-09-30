"""Forward transcript identity migration preserving historical rowid references."""

import sqlite3


def migrate_message_identity(db: sqlite3.Connection) -> None:
    indexes = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='messages' AND sql IS NOT NULL"
    ).fetchall()
    db.execute("""CREATE TABLE messages_identity (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT NOT NULL,
        session_id TEXT NOT NULL DEFAULT 'default',
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        telegram_message_id TEXT,
        telegram_message_ids TEXT NOT NULL DEFAULT '[]',
        created_at REAL NOT NULL
    )""")
    db.execute(
        "INSERT INTO messages_identity SELECT rowid,chat_id,session_id,role,content,"
        "telegram_message_id,telegram_message_ids,created_at FROM messages ORDER BY rowid"
    )
    db.execute("DROP TABLE messages")
    db.execute("ALTER TABLE messages_identity RENAME TO messages")
    for (sql,) in indexes:
        db.execute(sql)
    # Remove alternatives whose source turn is already absent or has been edited.
    db.execute(
        "DELETE FROM response_variants WHERE NOT EXISTS (SELECT 1 FROM messages m "
        "WHERE m.rowid=response_variants.user_rowid AND m.chat_id=response_variants.chat_id "
        "AND m.session_id=response_variants.session_id AND m.role='user' "
        "AND m.content=response_variants.user_content)"
    )
