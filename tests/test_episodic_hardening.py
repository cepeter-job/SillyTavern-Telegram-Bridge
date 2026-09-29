import sqlite3

from bridge import episodic_memory as episodic
from bridge.episodic_extraction import parse_episodic_candidates
from bridge.memory_service import MemoryService
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
    source = (
        '[{"kind":"secret","importance":0.95,"summary":"Mira knows the vault code.",'
        '"visibility":"restricted","known_by":["Mira"]},'
        '{"kind":"secret","importance":0.99,"summary":"Unknown secret.",'
        '"visibility":"restricted","known_by":[]},'
        '{"kind":"secret","importance":0.93,"summary":"Legacy unscoped secret."},'
        '{"kind":"fact","importance":0.90,"summary":"The gate is public.",'
        '"visibility":"shared","known_by":["Mira"]}]'
    )

    result = parse_episodic_candidates(source)

    assert [(item.kind, item.visibility, item.known_by, item.summary) for item in result] == [
        ("secret", "restricted", ("Mira",), "Mira knows the vault code."),
        ("fact", "shared", (), "The gate is public."),
    ]


def test_retrieval_filters_restricted_memory_by_active_character():
    db = _db()
    try:
        episodic.store_episodic_memory(
            db,
            "chat",
            "s1",
            kind="fact",
            importance=0.8,
            summary="The red key is publicly displayed.",
            source_start_rowid=1,
            source_end_rowid=8,
            visibility="shared",
        )
        episodic.store_episodic_memory(
            db,
            "chat",
            "s1",
            kind="secret",
            importance=0.95,
            summary="Mira hid the red key behind the portrait.",
            source_start_rowid=9,
            source_end_rowid=16,
            visibility="restricted",
            known_by=("Mira",),
        )
        db.commit()

        mira = episodic.episodic_context_for_prompt(db, "chat", _session(), _fields("Mira"), "red key")
        bob = episodic.episodic_context_for_prompt(db, "chat", _session(), _fields("Bob"), "red key")

        assert "publicly displayed" in mira
        assert "behind the portrait" in mira
        assert "publicly displayed" in bob
        assert "behind the portrait" not in bob
    finally:
        db.close()


def test_edit_prompt_suppresses_episodic_recall_before_history_rewrite():
    calls = []
    service = MemoryService(
        recall_context=lambda *_args, **_kwargs: "",
        summary_for_prompt=lambda *_args, **_kwargs: "summary",
        summary_state=lambda *_args, **_kwargs: ("summary", 10),
        retain_session=lambda *_args, **_kwargs: None,
        purge_session_memory=lambda *_args, **_kwargs: 0,
        episodic_context=lambda *_args, **_kwargs: calls.append(1) or "stale episode",
    )
    db = _db()
    try:
        context = service.prompt_context(
            db,
            "chat",
            _session(),
            _fields(),
            "edited text",
            edited_user_rowid=5,
        )

        assert context.episodic == ""
        assert calls == []
    finally:
        db.close()


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

        remaining = episodic.list_episodic_memories(db, "chat", "s1")
        assert removed == 2
        assert [item.summary for item in remaining] == ["early red key fact"]
    finally:
        db.close()
