"""Forward migration for autonomous durable-memory retry guards."""

import sqlite3

from bridge.memory_schema import _enqueue_sql
from bridge.memory_scope_schema import _repair_memory_mutation_triggers


def migrate_memory_retry_guard(db: sqlite3.Connection) -> None:
    """Refresh enqueue triggers so new source activity reopens parked work."""
    db.execute("DROP TRIGGER IF EXISTS memory_message_insert")
    db.execute(f"""CREATE TRIGGER memory_message_insert AFTER INSERT ON messages BEGIN
        {_enqueue_sql("NEW")} END""")
    _repair_memory_mutation_triggers(db)
