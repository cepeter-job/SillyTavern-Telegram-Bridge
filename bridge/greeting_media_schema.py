"""Keep greeting media receipts owned by their committed transcript message."""

import sqlite3


def migrate_greeting_media_cleanup(db: sqlite3.Connection) -> None:
    if not db.in_transaction:
        raise RuntimeError("Greeting media migration requires an active transaction")
    db.execute(
        "DELETE FROM meta WHERE (key GLOB 'greeting_photo:[0-9]*' "
        "OR key GLOB 'greeting_photo_url:[0-9]*') AND NOT EXISTS("
        "SELECT 1 FROM messages m WHERE meta.key='greeting_photo:'||m.rowid "
        "OR meta.key='greeting_photo_url:'||m.rowid)"
    )
    db.execute(
        "CREATE TRIGGER greeting_media_message_delete AFTER DELETE ON messages BEGIN "
        "DELETE FROM meta WHERE key IN ('greeting_photo:'||OLD.rowid,'greeting_photo_url:'||OLD.rowid); END"
    )
