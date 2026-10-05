import sqlite3

import pytest
from persisted_state_test_support import read_episodic_memories
from test_story_memory_scope import db as db

from bridge import episodic_memory as episodic
from bridge.episodic_extraction import parse_episodic_candidates
from bridge.schema import initialize_database_schema


def _db():
    db = sqlite3.connect(":memory:")
    initialize_database_schema(db)
    return db


def _session(session_id="s1"):
    return {"session_id": session_id, "model_id": "story::main"}


def _fields(name="Mira"):
    return {"name": name}


def test_parser_keeps_restricted_memory_only_with_explicit_known_by():
    result = parse_episodic_candidates(
        '[{"kind":"secret","importance":0.95,"summary":"Mira knows the vault code.",'
        '"visibility":"restricted","known_by":["Mira"]}]'
    )
    assert [(item.kind, item.visibility, item.known_by) for item in result] == [("secret", "restricted", ("mira",))]
    with pytest.raises(ValueError):
        parse_episodic_candidates('[{"kind":"secret","importance":0.93,"summary":"Unclassified secret."}]')


def test_retrieval_filters_restricted_memory_by_active_character(db):
    from test_story_memory_scope import accept, append

    append(db)
    accept(db, "The red key is publicly displayed.", audience=(), visibility="shared")
    append(db)
    accept(db, "Mira hid the red key behind the portrait.")
    mira = episodic.episodic_context_for_prompt(db, "c", _session("s"), _fields("Mira"), "red key")
    bob = episodic.episodic_context_for_prompt(db, "c", _session("s"), _fields("Bob"), "red key")
    assert "publicly displayed" in mira and "behind the portrait" in mira
    assert "publicly displayed" in bob and "behind the portrait" not in bob


def test_edit_prompt_keeps_earlier_eligible_episode_and_excludes_future(db):
    from test_story_memory_artifacts import service
    from test_story_memory_scope import accept, append

    append(db)
    accept(db, "early red key fact")
    edited = append(db)
    accept(db, "stale red key episode")
    context = service().prompt_context(db, "c", _session("s"), _fields(), "red key", edited_user_rowid=edited)
    assert "early red key fact" in context.episodic
    assert "stale" not in context.episodic


def test_invalidate_episodic_memories_removes_edited_and_later_ranges():
    db = _db()
    try:
        for start, end, summary in (
            (1, 4, "early red key fact"),
            (5, 8, "edited red key fact"),
            (9, 16, "later red key fact"),
        ):
            episodic.store_episodic_memory(
                db,
                "chat",
                "s1",
                kind="fact",
                importance=0.8,
                summary=summary,
                source_start_rowid=start,
                source_end_rowid=end,
            )
        db.commit()

        removed = episodic.invalidate_episodic_memories_from_row(db, "chat", "s1", 5)
        db.commit()

        remaining = read_episodic_memories(db, "chat", "s1")
        assert removed == 2
        assert [item.summary for item in remaining] == ["early red key fact"]
    finally:
        db.close()
