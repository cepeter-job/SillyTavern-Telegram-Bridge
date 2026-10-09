"""Only explicit shadow mode may observe statement diagnostics, never dispatch them."""

import pytest

from bridge.context_selection_runtime import choose_context_messages
from bridge.context_selection_store import prepare_context_selection
from bridge.memory_contracts import MemoryPromptContext
from bridge.settings import load_app_settings
from tools.hybrid_context_fixture import native_fixture


@pytest.fixture
def story(tmp_path):
    value = native_fixture(tmp_path, variant="commitment_dense")
    yield value
    value[0].close()


def test_statement_shadow_runtime_preserves_original_dispatch(story, tmp_path):
    db, scope, messages, _ = story
    settings = load_app_settings({"SILLYTAVERN_CONTEXT_SELECTION_MODE": "shadow"}, home=tmp_path)
    context = MemoryPromptContext(scope=scope, selection_query="turquoise astrolabe")
    prepared = prepare_context_selection(
        db,
        context,
        lambda: scope,
        app_settings=settings,
        validate_blocks=lambda _db, _scope, blocks: blocks,
    )
    messages[0]["_context_selection"] = prepared
    output, metrics = choose_context_messages(
        messages,
        app_settings=settings,
        chars_per_token=4,
        input_budget_tokens=100000,
    )
    assert metrics["hybrid_shadow_reason"] == "all_history_protected"
    assert metrics["hybrid_statement_reason"] == "semantic_review_required"
    assert metrics["hybrid_statement_candidate_tokens"] < metrics["original_tokens"]
    assert metrics["hybrid_statement_source_verified"] is True
    assert metrics["hybrid_statement_activation_allowed"] is False
    assert metrics["applied"] is False
    assert output[0]["content"] == messages[0]["content"]
    assert output[-1]["content"] == messages[-1]["content"]
    assert len(output) == len(messages)
    assert "PRIVATE_HYBRID_CANARY" not in str(metrics)


@pytest.mark.parametrize("mode", ["off", "enabled"])
def test_statement_probe_not_run_outside_shadow(story, tmp_path, mode):
    db, scope, messages, _ = story
    settings = load_app_settings({"SILLYTAVERN_CONTEXT_SELECTION_MODE": mode}, home=tmp_path)
    context = MemoryPromptContext(scope=scope, selection_query="turquoise astrolabe")
    prepared = prepare_context_selection(
        db,
        context,
        lambda: scope,
        app_settings=settings,
        validate_blocks=lambda _db, _scope, blocks: blocks,
    )
    messages[0]["_context_selection"] = prepared
    _output, stats = choose_context_messages(
        messages,
        app_settings=settings,
        chars_per_token=4,
        input_budget_tokens=100000,
    )
    assert "hybrid_statement_candidate_tokens" not in stats


def test_tampered_diagnostics_cannot_exfiltrate_private_text(story, tmp_path):
    _db, scope, messages, _ = story
    settings = load_app_settings({"SILLYTAVERN_CONTEXT_SELECTION_MODE": "shadow"}, home=tmp_path)

    def malicious(_messages, _ratio):
        return {
            "reason": "semantic_review_required",
            "candidate_tokens": 300,
            "omitted_turns": 6,
            "native_source_verified": True,
            "statement_candidate_tokens": -100,
            "statement_reason": "PRIVATE_HYBRID_CANARY",
            "statement_source_verified": "yes",
        }

    messages[0]["_context_selection"] = MemoryPromptContext(
        scope=scope,
        selection_hybrid_shadow=malicious,
    )
    _, stats = choose_context_messages(
        messages,
        app_settings=settings,
        chars_per_token=4,
        input_budget_tokens=100000,
    )
    assert "PRIVATE_HYBRID_CANARY" not in str(stats)
    assert stats["applied"] is False
    assert stats["hybrid_shadow_candidate_tokens"] == stats["original_tokens"]
