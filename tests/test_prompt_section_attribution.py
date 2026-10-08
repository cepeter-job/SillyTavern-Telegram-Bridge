"""The real builder attributes payload sections without persisting their text."""

import json
from types import SimpleNamespace

from settings_test_support import make_test_settings
from test_story_memory_scope import db as db

from bridge import generation
from bridge.context_diagnostics import context_stats_key
from bridge.metadata import get_meta


def _build(tmp_path, monkeypatch, *, deferred=True, stats=None):
    settings = make_test_settings(home=tmp_path, context_window_tokens=65536)
    monkeypatch.setattr(generation, "build_world_info", lambda *a, **k: "WORLD" * 4)
    fields = {
        "name": "Rowan",
        "first_mes": "",
        "post_history_instructions": "VOICE RULE",
        "system_prompt": "Protect agency.",
        "description": "CHARACTER PRIVATE",
        "personality": "Patient",
        "scenario": "A quiet station",
        "mes_example": "",
    }
    session = {"session_id": "s", "model_id": "synthetic", "persona_id": "", "world_file": ""}
    messages = generation.build_chat_messages(
        session,
        fields,
        "CURRENT PRIVATE",
        [("assistant", "OLD1"), ("assistant", "OLD2")],
        persona_service=SimpleNamespace(get=lambda *a: None),
        memory_context="m" * 8,
        episodic_context="e" * 8,
        npc_context="n" * 8,
        simulation_context="s" * 8,
        session_summary="u" * 8,
        scene_context="c" * 8,
        rag_context="r" * 8,
        app_settings=settings,
        defer_compaction=deferred,
        context_stats=stats,
    )
    return settings, session, messages


def test_actual_finalized_builder_persists_only_estimated_section_counts(db, tmp_path, monkeypatch):
    settings, session, messages = _build(tmp_path, monkeypatch)
    final = generation.finalize_generation_messages(
        db,
        "c",
        session,
        messages,
        {"max_tokens": 1800},
        app_settings=settings,
    )
    saved = json.loads(get_meta(db, context_stats_key("c", "s"), ""))
    assert saved["section_estimated_tokens"]["derived"] == 14
    assert saved["section_estimated_tokens"]["history"] == 2
    assert saved["section_estimated_tokens"]["world_info"] == 5
    assert saved["section_estimated_tokens"]["mandatory"] > 0
    assert saved["section_estimated_tokens"]["task"] > 0
    assert "PRIVATE" not in json.dumps(saved) and "VOICE RULE" not in json.dumps(saved)
    assert all(not key.startswith("_context_") for message in final for key in message)


def test_post_builder_contract_additions_count_as_task_input(db, tmp_path, monkeypatch):
    settings, session, messages = _build(tmp_path, monkeypatch)
    generation.finalize_generation_messages(
        db,
        "c",
        session,
        messages,
        {"max_tokens": 1800},
        app_settings=settings,
    )
    before = json.loads(get_meta(db, context_stats_key("c", "s"), ""))["section_estimated_tokens"]
    messages.append({"role": "system", "content": "Z" * 400})
    generation.finalize_generation_messages(
        db,
        "c",
        session,
        messages,
        {"max_tokens": 1800},
        app_settings=settings,
    )
    after = json.loads(get_meta(db, context_stats_key("c", "s"), ""))["section_estimated_tokens"]
    assert after == dict(before, task=before["task"] + 100)


def test_direct_builder_returns_accounting_without_metadata_on_wire(tmp_path, monkeypatch):
    stats = {}
    _, _, final = _build(tmp_path, monkeypatch, deferred=False, stats=stats)
    assert stats["section_estimated_tokens"]["derived"] == 14
    assert all(not key.startswith("_context_") for message in final for key in message)
