import sqlite3

from application_test_setup import make_test_persona_service
from settings_test_support import SettingsBuilder
from test_story_memory_scope import db as db

from bridge.context_compaction import compact_chat_messages
from bridge.episodic_memory import episodic_context_for_prompt
from bridge.generation import build_chat_messages
from bridge.memory_service import MemoryService
from bridge.schema import initialize_database_schema


def _db():
    db = sqlite3.connect(":memory:")
    initialize_database_schema(db)
    return db


def _session(session_id="s1"):
    return {
        "session_id": session_id,
        "model_id": "story::main",
        "persona_id": "",
        "world_file": "",
        "system_prompt": "",
        "author_note": "",
        "response_language": "auto",
    }


def _fields():
    return {
        "name": "Mira",
        "system_prompt": "",
        "description": "",
        "personality": "",
        "scenario": "",
        "mes_example": "",
        "first_mes": "",
        "post_history_instructions": "",
    }


def test_episodic_retrieval_is_query_relevant_and_session_scoped(db):
    from test_story_memory_scope import accept, append

    from bridge.memory_contracts import MemoryFact
    from bridge.memory_fact_store import accept_source_facts
    from bridge.memory_store import next_source_segment

    append(db, "Mira hid the red key in the attic.")
    accept(db, "Mira hid the red key in the attic.", audience=(), visibility="shared")
    append(db, "The station gates were permanently sealed.")
    accept(db, "The station gates were permanently sealed.", audience=(), visibility="shared")
    append(db, "The red key belongs to another session.", sid="other")
    accept_source_facts(
        db,
        next_source_segment(db, "c", "other", "episodes"),
        [
            MemoryFact("fact", 1.0, "The red key belongs to another session.", "shared"),
        ],
    )
    context = episodic_context_for_prompt(db, "c", _session("s"), _fields(), "Where is the red key?")
    assert "Mira hid the red key in the attic." in context
    assert "station gates" not in context
    assert "another session" not in context


def test_generation_injects_episodic_context_in_separate_untrusted_tag():
    messages = build_chat_messages(
        _session(),
        _fields(),
        "Where is the key?",
        [],
        persona_service=make_test_persona_service(),
        episodic_context="[fact] Mira hid the red key in the attic.",
        app_settings=SettingsBuilder().build(),
    )

    assert "Episodic memory policy" in messages[0]["content"]
    assert "<untrusted_episodic_memory>" in messages[-1]["content"]
    assert "Mira hid the red key" in messages[-1]["content"]
    assert "<untrusted_memory>" not in messages[-1]["content"]


def test_compaction_can_trim_episodic_context_before_summary():
    messages = [
        {
            "role": "system",
            "content": "Fixed instructions\n\n## Session continuity summary\n" + ("S" * 5000),
        },
        {
            "role": "user",
            "content": "<untrusted_episodic_memory>\n"
            + ("E" * 12000)
            + "\n</untrusted_episodic_memory>\n\nCurrent turn",
        },
    ]

    compacted, stats = compact_chat_messages(
        messages,
        budget_tokens=2048,
        min_recent_messages=1,
        app_settings=SettingsBuilder().build(),
    )

    assert stats["memory_trimmed"] is True
    assert "Current turn" in compacted[-1]["content"]
    assert "E" * 12000 not in compacted[-1]["content"]


def test_memory_service_keeps_episodic_context_separate():
    from bridge.memory_contracts import MemoryBlock, MemoryReadScope

    service = MemoryService(
        resolve_scope=lambda *a, **k: MemoryReadScope("chat", "s1", 1.0, 8, 0, ("mira",)),
        scoped_recall=lambda *a: MemoryBlock("hindsight"),
        scoped_episodes=lambda *a: MemoryBlock("episode"),
        scoped_summary=lambda *a: MemoryBlock("continuity"),
        scoped_scene=lambda *a: MemoryBlock("scene"),
        validate_blocks=lambda _db, _scope, blocks: blocks,
        summary_state=lambda *_args: ("continuity", 8),
        retain_session=lambda *_args: None,
        purge_session_memory=lambda *_args: 0,
    )
    db = _db()
    try:
        context = service.prompt_context(db, "chat", _session(), _fields(), "red key")
        assert context.recall == "hindsight"
        assert context.summary == "continuity"
        assert context.episodic == "episode"
    finally:
        db.close()
