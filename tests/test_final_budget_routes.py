"""Real story entrypoints gate the final NovelTurn request before side effects."""

import json
from types import SimpleNamespace

import pytest
from application_test_setup import (
    make_test_delivery_port,
    make_test_group_service,
    make_test_memory_service,
    make_test_persona_service,
    make_test_provider_port,
    make_test_rag_service,
)
from memory_runtime_test_support import isolated_memory_runtime as isolated_memory_runtime
from settings_test_support import make_test_settings
from test_story_memory_scope import append
from test_story_memory_scope import db as db

from bridge import continuation, edit_messages, generation, image_messages, message_commands, regeneration
from bridge.context_compaction import ContextWindowBudgetError, estimate_message_tokens
from bridge.context_diagnostics import context_stats_key
from bridge.light_novel_format import add_inline_contract
from bridge.metadata import get_meta


class PromptDispatched(Exception):
    pass


@pytest.mark.parametrize("route", ["ordinary", "edit", "regen", "continue", "image"])
@pytest.mark.parametrize("overflow", [True, False])
def test_five_actual_paths_gate_after_novel_contract_before_generation(db, tmp_path, monkeypatch, route, overflow):
    if not overflow:
        append(db, "OLD REMOVABLE " + "o" * 30000)
    user = append(db, "CURRENT")
    db.execute(
        "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('c','s','assistant','EXACT ENDING',4)"
    )
    db.commit()
    before = db.execute("SELECT id,role,content FROM messages ORDER BY id").fetchall()
    settings = make_test_settings(home=tmp_path, context_window_tokens=4096 if overflow else 8000)
    session = {"session_id": "s", "model_id": "synthetic", "persona_id": "", "world_file": ""}
    fields = {"name": "Synthetic", "first_mes": "", "post_history_instructions": ""}
    owner = {
        "ordinary": message_commands,
        "edit": edit_messages,
        "regen": regeneration,
        "continue": continuation,
        "image": image_messages,
    }[route]
    monkeypatch.setattr(generation, "build_system_prompt", lambda *a, **k: "Fixed instructions.")
    monkeypatch.setattr(owner, "narrative_context_for_session", lambda *a, **k: "")
    for module in (owner, generation):
        if hasattr(module, "get_generation_settings"):
            monkeypatch.setattr(module, "get_generation_settings", lambda *a: {"max_tokens": 1800})
    if hasattr(owner, "require_started"):
        monkeypatch.setattr(owner, "require_started", lambda *a: True)
    if hasattr(owner, "send_typing"):
        monkeypatch.setattr(owner, "send_typing", lambda *a: None)
    placeholders = []
    if hasattr(owner, "telegram_request"):
        monkeypatch.setattr(owner, "telegram_request", lambda *a, **k: placeholders.append(a) or {})
    turn = SimpleNamespace(
        messages=lambda messages, language: add_inline_contract(
            messages, 4, language, narrative_policy="Mandatory policy " + "x" * 9000 if overflow else "Policy"
        ),
        record=SimpleNamespace(strategy="b"),
    )
    if route == "ordinary":
        monkeypatch.setattr(owner, "prepare_turn", lambda *a: object())
        monkeypatch.setattr(owner, "NovelTurn", lambda *a, **k: turn)
    else:
        monkeypatch.setattr(owner, "begin_novel_turn", lambda *a: turn)
    calls = []

    def backend(*args, **kwargs):
        calls.append((args, kwargs))
        if overflow:
            pytest.fail("Oversized final request reached the provider")
        if route == "ordinary":
            kwargs["stream_callback"]("VISIBLE FIRST")
        raise PromptDispatched

    kwargs = dict(
        provider_port=make_test_provider_port(generate_backend=backend),
        memory_service=make_test_memory_service(),
        npc_service=SimpleNamespace(context_for_prompt=lambda *a, **k: ""),
        persona_service=make_test_persona_service(),
        app_settings=settings,
        rag_service=make_test_rag_service(),
    )
    with pytest.raises(ContextWindowBudgetError if overflow else PromptDispatched):
        if route == "ordinary":
            message_commands.generate_and_store_reply(
                db,
                "",
                "",
                fields,
                "c",
                "CURRENT",
                session,
                "s",
                "synthetic",
                None,
                "",
                None,
                None,
                group_service=make_test_group_service(app_settings=settings),
                **kwargs,
            )
        elif route == "edit":
            edit_messages.regenerate_edited_turn(db, "", "", session, fields, "c", user, "CURRENT", **kwargs)
        elif route == "regen":
            regeneration.regenerate_last(
                db, "", "", session, fields, "c", delivery_port=make_test_delivery_port(), **kwargs
            )
        elif route == "continue":
            continuation.continue_last(
                db, "", "", session, fields, "c", delivery_port=make_test_delivery_port(), **kwargs
            )
        else:
            image_messages.process_image_message(
                db,
                "",
                "",
                session,
                fields,
                "c",
                "CURRENT",
                b"synthetic",
                group_service=make_test_group_service(app_settings=settings),
                group_director_service=SimpleNamespace(),
                **kwargs,
            )
    if overflow:
        assert not calls and not placeholders
    assert db.execute("SELECT id,role,content FROM messages ORDER BY id").fetchall() == before
    assert db.execute("SELECT count(*) FROM response_variants").fetchone()[0] == 0
    stats = json.loads(get_meta(db, context_stats_key("c", "s"), ""))
    assert stats["over_budget"] is overflow and stats["requested_output_tokens"] == 1800
    if not overflow:
        assert len(calls) == 1
        if route == "ordinary":
            assert placeholders == [("", "sendMessage", {"chat_id": "c", "text": "VISIBLE FIRST"})]
        payload = calls[0][0][2]
        assert "Light Novel response contract" in payload[0]["content"]
        assert "OLD REMOVABLE" not in str(payload)
        assert "CURRENT" in str(payload) or route == "continue"
        assert all("_context_optional" not in message for message in payload)
        assert calls[0][1]["settings"]["max_tokens"] == 1800
        assert stats["final_tokens"] == estimate_message_tokens(payload)
        assert stats["budget_tokens"] == 8000 - 1800 - 512
        assert stats["final_tokens"] <= stats["budget_tokens"]
        assert stats["dropped_history"] >= 1
        if route == "continue":
            assert any(message.get("content") == "EXACT ENDING" for message in payload)
        if route == "image":
            assert payload[-1]["content"][1]["image_url"]["url"] == "data:image/jpeg;base64,c3ludGhldGlj"
