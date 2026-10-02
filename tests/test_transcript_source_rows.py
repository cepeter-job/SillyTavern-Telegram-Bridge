"""Shared transcript mechanics preserve domain bounds, ordering and isolation."""

import sqlite3

import pytest
from application_test_setup import make_test_provider_port
from source_test_support import top_level_functions
from test_light_novel_storage import novel_db as novel_db

from bridge import memory_curator, scene_state, transcript_repository


def query(db, chat_id="chat", session_id="session", **kwargs):
    reader = getattr(transcript_repository, "recent_transcript_rows", None)
    assert callable(reader), "The transcript repository must own the bounded reader"
    return reader(db, chat_id, session_id, **kwargs)


@pytest.fixture
def transcript():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE messages(chat_id TEXT,session_id TEXT,role TEXT,content TEXT,created_at REAL)")
    for chat, session, content, stamp in (
        ("chat", "session", "first", 1),
        ("chat", "session", "same-time-first", 3),
        ("chat", "session", "same-time-second", 3),
        ("other", "session", "other-chat", 8),
        ("chat", "other", "other-session", 9),
        ("chat", "session", "earlier-timestamp", 2),
        ("chat", "session", "last", 4),
    ):
        db.execute("INSERT INTO messages VALUES(?,?,?,?,?)", (chat, session, "user", content, stamp))
    db.commit()
    yield db
    db.close()


def test_recent_rows_are_bounded_chronological_and_tie_broken(transcript):
    rows = query(transcript, limit=3)
    assert [row[2] for row in rows] == ["same-time-first", "same-time-second", "last"]
    assert [row[0] for row in rows] == [2, 3, 7]
    assert not transcript.in_transaction


def test_through_rowid_is_inclusive_before_limit(transcript):
    rows = query(transcript, limit=2, through_rowid=3)
    assert [row[0] for row in rows] == [2, 3]
    assert query(transcript, limit=20, through_rowid=0) == []


def test_scope_values_are_parameters_not_sql(transcript):
    assert query(transcript, "chat' OR 1=1 --", limit=20) == []
    assert query(transcript, session_id="session' OR 1=1 --", limit=20) == []
    assert len(query(transcript, limit=20)) == 5


def test_reader_does_not_commit_callers_transaction(transcript):
    transcript.execute("BEGIN")
    assert query(transcript, limit=1)[0][2] == "last"
    assert transcript.in_transaction
    transcript.rollback()


@pytest.mark.parametrize("limit", (0, -1, True, 1.5, "2"))
def test_invalid_limit_is_rejected_before_sql(limit):
    with pytest.raises(ValueError, match="positive integer"):
        query(object(), limit=limit)


@pytest.mark.parametrize(
    "owner,retired,entry,limit",
    (
        (memory_curator, "_curator_source_rows", "curate_memory_now", 20),
        (scene_state, "_scene_state_source_rows", "refresh_scene_state_now", 16),
    ),
)
def test_domains_use_one_reader_with_independent_limits(novel_db, monkeypatch, owner, retired, entry, limit):
    assert retired not in top_level_functions(owner.__name__.split(".")[-1] + ".py")
    assert owner.recent_transcript_rows is transcript_repository.recent_transcript_rows
    calls = []
    monkeypatch.setattr(owner, "recent_transcript_rows", lambda *a, **kw: calls.append((a, kw)) or [])
    db, session, settings = novel_db
    result = getattr(owner, entry)(
        db,
        "",
        "chat",
        session,
        "Alice",
        17,
        provider_port=make_test_provider_port(),
        app_settings=settings,
    )
    assert result is None
    assert calls == [((db, "chat", session["session_id"]), {"limit": limit, "through_rowid": 17})]
