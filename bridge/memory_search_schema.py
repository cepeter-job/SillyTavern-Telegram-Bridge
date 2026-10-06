"""Rebuildable lexical index; membership never grants prompt authority."""

import sqlite3


def migrate_memory_search(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE VIRTUAL TABLE memory_episode_fts USING fts5("
        "summary, content='episodic_memories', content_rowid='memory_id', tokenize='unicode61')"
    )
    db.execute(
        "CREATE TRIGGER memory_episode_fts_insert AFTER INSERT ON episodic_memories BEGIN "
        "INSERT INTO memory_episode_fts(rowid,summary) VALUES(NEW.memory_id,NEW.summary); END"
    )
    db.execute(
        "CREATE TRIGGER memory_episode_fts_delete AFTER DELETE ON episodic_memories BEGIN "
        "INSERT INTO memory_episode_fts(memory_episode_fts,rowid,summary) "
        "VALUES('delete',OLD.memory_id,OLD.summary); END"
    )
    db.execute(
        "CREATE TRIGGER memory_episode_fts_update AFTER UPDATE OF summary,memory_id ON episodic_memories BEGIN "
        "INSERT INTO memory_episode_fts(memory_episode_fts,rowid,summary) "
        "VALUES('delete',OLD.memory_id,OLD.summary); "
        "INSERT INTO memory_episode_fts(rowid,summary) VALUES(NEW.memory_id,NEW.summary); END"
    )
    db.execute("INSERT INTO memory_episode_fts(memory_episode_fts) VALUES('rebuild')")
