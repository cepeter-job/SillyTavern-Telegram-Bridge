"""Full-corpus local recall stays bounded, source-backed and reader-scoped."""

import sqlite3

import pytest
from test_story_memory_artifacts import service, write_artifacts
from test_story_memory_scope import accept, append, read, scope
from test_story_memory_scope import db as db
from test_story_memory_scope import forbid_real_hindsight_client as forbid_real_hindsight_client

from bridge.memory_contracts import MemoryBlock
from bridge.memory_scope_store import ranked_fact_block
from bridge.migrations import run_migrations
from bridge.schema import SCHEMA_MIGRATIONS, initialize_database_schema


def test_relevant_evidence_survives_beyond_200_episodes_and_100_messages(db):
    first = append(db, "The obsidian compass points to the harbor. PRIVATE PASSCODE is 1234.")
    _, memory_id = accept(db, "The obsidian compass points to the harbor.", audience=(), visibility="shared")
    for number in range(250):
        append(db, f"The weather report number {number}.")
        accept(db, f"Weather report number {number}.", audience=(), visibility="shared")
    statements = []
    db.set_trace_callback(statements.append)
    block = read(db, scope(db, "Bob"), "obsidian compass")
    db.set_trace_callback(None)
    assert "obsidian compass" in block.text
    assert "PRIVATE" not in block.text and "1234" not in block.text
    assert f"message {first}" in block.text
    assert [item.memory_id for item in block.evidence] == [memory_id]
    assert any("MATCH" in query for query in statements)
    assert len(statements) < 100


def test_reader_filter_precedes_rank_limit_and_late_grants_stay_historical(db):
    first = append(db, "A public copper compass.")
    accept(db, "The copper compass is public.", audience=(), visibility="shared")
    for number in range(240):
        append(db, f"Mira alone hears a copper compass secret {number}.")
        accept(db, f"Copper compass secret {number}.", audience=("Mira",))
    result = read(db, scope(db, "Bob", first), "copper compass")
    assert "public" in result.text and "secret" not in result.text
    assert len(result.evidence) == 1
    append(db, "Bob is told the hidden compass route.")
    accept(db, "The copper compass opens a hidden route.", audience=("Bob",))
    assert "hidden" not in read(db, scope(db, "Bob", first), "compass").text
    assert "hidden" in read(db, scope(db, "Bob"), "compass").text


@pytest.mark.parametrize("query", ["continue", "2"])
def test_continuation_expands_only_authorized_scene_and_cast(db, query):
    first = append(db, "The obsidian compass belongs at the harbor.")
    accept(db, "The obsidian compass points to the harbor.", audience=(), visibility="shared")
    write_artifacts(db, first, scene="Harbor; goal: recover obsidian compass")
    queries = []

    def recall(db, read_scope, expanded):
        queries.append(expanded)
        return MemoryBlock(channel="recall")

    result = service(recall).prompt_context(db, "c", {"session_id": "s"}, {"name": "Bob"}, query)
    assert "obsidian compass" in result.episodic
    assert len(queries) == 1
    assert "PRIVATE PASSCODE" not in queries[0]
    assert "bob" in queries[0].casefold() and "compass" in queries[0]
    assert len(queries[0]) <= 2048


def test_semantic_and_lexical_candidates_fuse_one_local_identity(db):
    append(db)
    _, memory_id = accept(db, audience=(), visibility="shared")
    document = db.execute("SELECT document_id FROM memory_fact_index WHERE memory_id=?", (memory_id,)).fetchone()[0]
    db.execute("UPDATE memory_fact_index SET state='retained'")
    db.commit()
    result = service(
        lambda db, read_scope, query: ranked_fact_block(db, read_scope, [document, document])
    ).prompt_context(db, "c", {"session_id": "s"}, {"name": "Bob"}, "silver key")
    assert (result.recall + result.episodic).count("silver key") == 1
    assert [item.memory_id for item in result.evidence] == [memory_id]


def test_forward_index_backfill_update_delete_and_rebuild():
    connection = sqlite3.connect(":memory:")
    run_migrations(connection, tuple(item for item in SCHEMA_MIGRATIONS if item.version <= 21))
    connection.execute(
        "INSERT INTO episodic_memories(chat_id,session_id,kind,importance,summary,"
        "source_start_rowid,source_end_rowid,created_at) VALUES('c','s','fact',1,'amber lantern',1,1,1)"
    )
    connection.commit()
    initialize_database_schema(connection)
    assert connection.execute(
        "SELECT rowid FROM memory_episode_fts WHERE memory_episode_fts MATCH 'amber'"
    ).fetchall() == [(1,)]
    connection.execute("UPDATE episodic_memories SET summary='violet lantern' WHERE memory_id=1")
    assert (
        connection.execute("SELECT rowid FROM memory_episode_fts WHERE memory_episode_fts MATCH 'amber'").fetchall()
        == []
    )
    assert connection.execute(
        "SELECT rowid FROM memory_episode_fts WHERE memory_episode_fts MATCH 'violet'"
    ).fetchall() == [(1,)]
    connection.execute("INSERT INTO memory_episode_fts(memory_episode_fts) VALUES('rebuild')")
    assert connection.execute(
        "SELECT rowid FROM memory_episode_fts WHERE memory_episode_fts MATCH 'violet'"
    ).fetchall() == [(1,)]
    connection.execute("DELETE FROM episodic_memories WHERE memory_id=1")
    assert (
        connection.execute("SELECT rowid FROM memory_episode_fts WHERE memory_episode_fts MATCH 'violet'").fetchall()
        == []
    )
    connection.close()


def test_query_syntax_and_size_cannot_expand_work_or_escape_scope(db):
    append(db, "A compass remains available.")
    accept(db, "A compass remains available.", audience=(), visibility="shared")
    statements = []
    db.set_trace_callback(statements.append)
    result = read(db, scope(db, "Bob"), 'compass " OR * NEAR(' + "word " * 20000)
    db.set_trace_callback(None)
    assert "compass" in result.text
    matches = [query for query in statements if " MATCH " in query]
    assert len(matches) <= 1
    assert matches and len(matches[0]) < 8000
