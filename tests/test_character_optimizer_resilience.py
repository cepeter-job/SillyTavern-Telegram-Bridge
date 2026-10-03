"""Regression tests for resilient character optimizer previews."""

from __future__ import annotations

import json
import logging

from application_test_setup import ensure_application_extensions
from test_character_mutation_safety import _card_png
from test_character_mutation_safety import card_context as card_context

ensure_application_extensions()

from bridge import character_optimizer_callbacks, character_quality
from bridge.provider_port import ProviderPort


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


def test_optimizer_preview_validation_logs_exact_reason_and_sanitizes_user_message(
    card_context, monkeypatch, caplog
):
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
