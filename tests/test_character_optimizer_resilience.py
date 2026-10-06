"""Regression tests for resilient character optimizer previews."""

from __future__ import annotations

import hashlib
import json
import logging

from application_test_setup import ensure_application_extensions, make_test_application_services
from character_test_support import card_context as card_context
from character_test_support import card_png as _card_png

ensure_application_extensions()

from bridge import (
    character_optimizer_callbacks,
    character_optimizer_input,
    character_optimizer_panels,
    character_quality,
    input_flows,
)
from bridge.provider_port import ProviderPort
from bridge.request_types import RequestContext


def test_optimizer_repairs_invalid_json_once(card_context, monkeypatch):
    db, ctx, _ = card_context
    calls = []
    responses = iter(
        [
            "I improved the card, but this is not valid JSON.",
            json.dumps({"description": "repaired description", "personality": "dry wit"}),
        ]
    )

    def generate(_api_key, _model, messages, **_kwargs):
        calls.append(messages)
        return next(responses)

    monkeypatch.setattr(character_quality, "task_model_for_session", lambda *a, **k: "utility::fixture")
    result = character_quality.optimize_character(
        db,
        "chat",
        {"session_id": ctx.session_id, "model_id": "story::fixture"},
        {"name": "Alice", "description": "original"},
        provider_port=ProviderPort(generate),
        app_settings=ctx.app_settings,
    )

    assert result == {"description": "repaired description", "personality": "dry wit"}
    assert len(calls) == 2
    repair_prompt = "\n".join(str(message["content"]) for message in calls[1])
    assert "Return only valid JSON" in repair_prompt
    assert "do not rewrite" in repair_prompt.casefold()
    assert "I improved the card, but this is not valid JSON." in repair_prompt


def test_optimizer_preview_validation_logs_exact_reason_and_sanitizes_user_message(card_context, monkeypatch, caplog):
    db, ctx, _ = card_context
    target = ctx.app_settings.character_dir / "Alice.png"
    target.write_bytes(_card_png("Alice", "original"))
    outputs = []

    monkeypatch.setattr(
        character_optimizer_callbacks,
        "resolve_dynamic_callback_token",
        lambda *_a, **_k: "Alice.png",
    )

    def reject_preview(*_args, **_kwargs):
        raise ValueError("Optimizer returned invalid JSON after one repair attempt.")

    monkeypatch.setattr(character_optimizer_callbacks, "prepare_character_optimization", reject_preview)
    monkeypatch.setattr(
        character_optimizer_callbacks,
        "send_panel_request",
        lambda _token, _method, payload, **_kwargs: outputs.append(payload),
    )

    with caplog.at_level(logging.WARNING):
        handled = character_optimizer_callbacks._handle_prepare(
            db,
            "token",
            {"id": "callback"},
            lambda *_args: None,
            "characteroptimizeauto:any-token",
            "chat",
            {"message_id": 55},
            {"session_id": ctx.session_id},
            provider_port=ProviderPort(lambda *_a, **_k: ""),
            request_context=ctx,
        )

    assert handled is True
    assert outputs[-1]["text"] == (
        "Optimization response was received, but the preview could not be validated.\n\n"
        "Reason: invalid optimizer JSON response.\n\n"
        "The installed card is unchanged."
    )
    assert "Optimizer returned invalid JSON after one repair attempt." in caplog.text
    assert target.read_bytes() == _card_png("Alice", "original")


def test_two_actors_keep_independent_optimizer_suggestion_state(card_context, monkeypatch):
    db, ctx, _ = card_context
    original = _card_png("Alice", "installed original")
    target = ctx.app_settings.character_dir / "Alice.png"
    target.write_bytes(original)
    digest = hashlib.sha256(original).hexdigest()
    prompts: list[str] = []
    previews: list[str] = []
    model_prompts: list[str] = []

    def generate(*args, **kwargs):
        model_prompts.append("\n".join(str(message.get("content") or "") for message in args[2]))
        return json.dumps({"description": f"draft {len(model_prompts)}"})

    port = ProviderPort(generate)
    services = make_test_application_services(app_settings=ctx.app_settings, provider=port)
    session = services.session.create(db, "chat", ctx.app_settings.default_model, session_id="session")
    monkeypatch.setattr(character_quality, "task_model_for_session", lambda *a, **k: "utility::fixture")
    monkeypatch.setattr(character_optimizer_input, "discard_panel_binding", lambda *a, **k: None)
    monkeypatch.setattr(character_optimizer_input, "close_panel_message", lambda *a, **k: None)
    monkeypatch.setattr(character_optimizer_input, "delete_pending_input_prompts", lambda *a, **k: None)
    monkeypatch.setattr(character_optimizer_input, "send_text", lambda _t, _c, text: prompts.append(text) or [700])
    monkeypatch.setattr(
        character_optimizer_panels,
        "send_panel_message",
        lambda _t, _c, text, _markup, *a, **k: previews.append(text),
    )
    callback = {"message": {"message_id": 55, "chat": {"id": "chat"}}}
    alice = RequestContext(db, session["session_id"], "alice", app_settings=ctx.app_settings)
    bob = RequestContext(db, session["session_id"], "bob", app_settings=ctx.app_settings)

    character_optimizer_input.start_character_optimizer_suggestion_input(
        db, "token", "chat", "Alice.png", digest, callback, request_context=alice
    )
    character_optimizer_input.start_character_optimizer_suggestion_input(
        db, "token", "chat", "Alice.png", digest, callback, request_context=bob
    )

    for context, suggestion in ((alice, "make her dry and sarcastic"), (bob, "make the greeting shorter")):
        handled = input_flows.handle_pending_input(
            db,
            "token",
            "chat",
            session,
            suggestion,
            fields={"name": "Alice"},
            handle_session_name=lambda *a, **k: False,
            group_service=services.group,
            provider_port=port,
            memory_service=services.memory,
            npc_service=services.npc,
            persona_service=services.persona,
            request_context=context,
            rag_service=services.rag,
        )
        assert handled is True

    assert len(model_prompts) == 2
    assert "make her dry and sarcastic" in model_prompts[0]
    assert "make the greeting shorter" in model_prompts[1]
    assert len(previews) == 2
