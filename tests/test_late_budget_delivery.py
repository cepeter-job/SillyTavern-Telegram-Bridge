"""An admitted assembly may still fail a real smaller provider attempt."""

import json
import urllib.error
from functools import partial
from types import SimpleNamespace

import pytest
from application_test_setup import (
    make_test_group_service,
    make_test_memory_service,
    make_test_persona_service,
    make_test_rag_service,
)
from memory_runtime_test_support import isolated_memory_runtime as isolated_memory_runtime
from test_provider_attempt_budget import setup_route
from test_story_memory_scope import db as db

from bridge import generation, message_commands, provider_transport
from bridge.context_compaction import ContextWindowBudgetError
from bridge.context_diagnostics import context_stats_key
from bridge.light_novel_format import add_inline_contract
from bridge.metadata import get_meta
from bridge.provider_port import ProviderPort


def test_ordinary_late_fallback_budget_failure_leaves_no_progress_or_accepted_turn(db, tmp_path, monkeypatch):
    settings, router = setup_route(
        tmp_path,
        window=16000,
        extra={
            "small": {
                "models": ["synthetic"],
                "transport": "openai_compatible",
                "context_window_tokens": 4096,
                "token_estimate_chars_per_token": 1.5,
                "api_endpoint": "https://example.com/v1",
                "api_key_env": "SYNTHETIC_KEY",
            },
        },
    )
    calls = []

    def unavailable(req, **kwargs):
        calls.append(json.loads(req.data))
        if len(calls) > 1:
            pytest.fail("The inadmissible fallback reached its provider")
        raise urllib.error.URLError("Synthetic first provider unavailable")

    monkeypatch.setattr(provider_transport, "strict_urlopen", unavailable)
    policy = SimpleNamespace(
        candidates=lambda *a: ("p::synthetic", "small::synthetic"),
        begin=lambda selection: SimpleNamespace(selection=selection),
        cancel=lambda *a: None,
        fail=lambda *a: None,
        succeed=lambda *a: None,
    )
    port = ProviderPort(
        generate_backend=partial(provider_transport.generate_provider_text, router, app_settings=settings),
        policy=policy,
    )
    monkeypatch.setattr(generation, "build_system_prompt", lambda *a, **k: "Fixed instructions.")
    monkeypatch.setattr(message_commands, "narrative_context_for_session", lambda *a, **k: "")
    monkeypatch.setattr(message_commands, "get_generation_settings", lambda *a: {"max_tokens": 1800})
    monkeypatch.setattr(message_commands, "require_started", lambda *a: True)
    monkeypatch.setattr(message_commands, "send_typing", lambda *a: None)
    turn = SimpleNamespace(
        messages=lambda messages, language: add_inline_contract(messages, 4, language, narrative_policy="Policy"),
        record=SimpleNamespace(strategy="b"),
    )
    monkeypatch.setattr(message_commands, "prepare_turn", lambda *a: object())
    monkeypatch.setattr(message_commands, "NovelTurn", lambda *a, **k: turn)
    telegram_messages = {}

    def telegram(_token, method, payload):
        if method == "sendMessage":
            telegram_messages[77] = payload["text"]
            return {"message_id": 77}
        if method == "deleteMessage":
            telegram_messages.pop(payload["message_id"], None)
        elif method == "editMessageText":
            telegram_messages[payload["message_id"]] = payload["text"]
        return {}

    monkeypatch.setattr(message_commands, "telegram_request", telegram)
    before = db.execute("SELECT id,role,content FROM messages").fetchall()
    with pytest.raises(ContextWindowBudgetError) as error:
        message_commands.generate_and_store_reply(
            db,
            "synthetic",
            "",
            {"name": "Synthetic", "first_mes": "", "post_history_instructions": ""},
            "c",
            "CURRENT " + "x" * 6000,
            {"session_id": "s", "model_id": "p::synthetic", "persona_id": "", "world_file": ""},
            "s",
            "p::synthetic",
            None,
            "",
            None,
            None,
            group_service=make_test_group_service(app_settings=settings),
            provider_port=port,
            memory_service=make_test_memory_service(),
            npc_service=SimpleNamespace(context_for_prompt=lambda *a, **k: ""),
            persona_service=make_test_persona_service(),
            app_settings=settings,
            rag_service=make_test_rag_service(),
        )
    assert len(calls) == 1 and "Light Novel response contract" in calls[0]["messages"][0]["content"]
    assert error.value.stats["window_tokens"] == 4096
    assert db.execute("SELECT id,role,content FROM messages").fetchall() == before
    assert db.execute("SELECT count(*) FROM response_variants").fetchone() == (0,)
    stats = json.loads(get_meta(db, context_stats_key("c", "s"), ""))
    assert stats["over_budget"] is True and stats["model"] == "small::synthetic"
    assert not telegram_messages, "Late admission failure must not leave an orphan Generating message"
