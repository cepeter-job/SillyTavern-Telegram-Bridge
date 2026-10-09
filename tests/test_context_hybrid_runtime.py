"""Runtime may observe hybrid estimates but must never dispatch a hybrid prompt."""

import copy
from dataclasses import replace

import pytest

from bridge.context_hybrid_shadow import evaluate_hybrid_shadow
from bridge.context_selection_runtime import ContextSelectionStaleError, choose_context_messages
from bridge.context_selection_store import prepare_context_selection
from bridge.memory_contracts import MemoryPromptContext
from bridge.settings import load_app_settings
from tools.hybrid_context_fixture import native_fixture


@pytest.fixture
def story(tmp_path):
    data = native_fixture(tmp_path)
    yield data
    data[0].close()


@pytest.mark.parametrize("mode", ["off", "enabled"])
def test_nonshadow_modes_do_not_run_the_hybrid_probe(story, tmp_path, mode):
    _db, scope, messages, _ = story

    def unexpected(*_args):
        pytest.fail("Hybrid probe must not run outside explicit shadow mode")

    context = MemoryPromptContext(scope=scope, selection_hybrid_shadow=unexpected)
    messages[0]["_context_selection"] = context
    config = load_app_settings({"SILLYTAVERN_CONTEXT_SELECTION_MODE": mode}, home=tmp_path)
    output, stats = choose_context_messages(
        messages, app_settings=config, chars_per_token=4, input_budget_tokens=100000
    )
    assert "hybrid_shadow_candidate_tokens" not in stats
    assert output[0]["content"] == messages[0]["content"]
    assert len(output) == len(messages)


def test_prepared_runtime_shadow_preserves_full_baseline_and_reports_only_metrics(story, tmp_path):
    db, scope, messages, _ = story
    config = load_app_settings({"SILLYTAVERN_CONTEXT_SELECTION_MODE": "shadow"}, home=tmp_path)
    context = MemoryPromptContext(scope=scope, selection_query="turquoise astrolabe")
    prepared = prepare_context_selection(
        db, context, lambda: scope, app_settings=config, validate_blocks=lambda _db, _scope, blocks: blocks
    )
    assert callable(prepared.selection_hybrid_shadow)
    messages[0]["_context_selection"] = prepared
    before = copy.deepcopy(messages)
    output, stats = choose_context_messages(
        messages, app_settings=config, chars_per_token=4, input_budget_tokens=100000
    )
    expected = [
        {key: value for key, value in message.items() if key not in {"_context_selection", "_context_history_index"}}
        for message in messages
    ]
    assert output == expected
    assert messages == before
    assert stats["hybrid_shadow_candidate_tokens"] < stats["original_tokens"]
    assert stats["hybrid_shadow_reason"] == "semantic_review_required"
    assert stats["hybrid_shadow_source_verified"] is True
    assert stats["applied"] is False
    assert "PRIVATE_HYBRID_CANARY" not in str(stats)


@pytest.mark.parametrize("mode", ["off", "shadow", "enabled"])
def test_hybrid_evaluation_packet_cannot_be_used_as_a_dispatch_prompt(story, tmp_path, mode):
    db, scope, messages, _ = story
    candidate = evaluate_hybrid_shadow(db, scope, messages, query="astrolabe").candidate_messages
    assert candidate != messages
    settings = load_app_settings({"SILLYTAVERN_CONTEXT_SELECTION_MODE": mode}, home=tmp_path)
    with pytest.raises(ValueError, match="shadow-only"):
        choose_context_messages(candidate, app_settings=settings, chars_per_token=4, input_budget_tokens=100000)


def test_source_change_during_shadow_observation_cannot_dispatch_old_baseline(story, tmp_path):
    db, scope, messages, rows = story
    settings = load_app_settings({"SILLYTAVERN_CONTEXT_SELECTION_MODE": "shadow"}, home=tmp_path)
    context = MemoryPromptContext(scope=scope)
    prepared = prepare_context_selection(
        db, context, lambda: scope, app_settings=settings, validate_blocks=lambda _db, _scope, blocks: blocks
    )
    original = prepared.selection_hybrid_shadow

    def changed(*args):
        result = original(*args)
        db.execute("UPDATE messages SET content='Rewritten promise.' WHERE id=?", (rows[4][0],))
        db.commit()
        return result

    # This resolver rechecks the real current scope rather than returning a static object.
    from bridge.memory_scope_store import resolve_memory_scope

    prepared = prepare_context_selection(
        db,
        context,
        lambda: resolve_memory_scope(db, "c", {"session_id": "s"}, {"name": "Rowan"}),
        app_settings=settings,
        validate_blocks=lambda _db, _scope, blocks: blocks,
    )
    prepared = replace(prepared, selection_hybrid_shadow=changed)
    messages[0]["_context_selection"] = prepared
    with pytest.raises(ContextSelectionStaleError):
        choose_context_messages(messages, app_settings=settings, chars_per_token=4, input_budget_tokens=100000)


def test_invalid_probe_diagnostics_cannot_leak_text_or_claim_impossible_savings(story, tmp_path):
    _db, scope, messages, _ = story
    settings = load_app_settings({"SILLYTAVERN_CONTEXT_SELECTION_MODE": "shadow"}, home=tmp_path)

    def invalid(*_args):
        return {
            "reason": "PRIVATE_PROBE_TEXT_CANARY",
            "candidate_tokens": -100,
            "omitted_turns": 999999,
            "native_source_verified": "trusted",
        }

    messages[0]["_context_selection"] = MemoryPromptContext(scope=scope, selection_hybrid_shadow=invalid)
    output, stats = choose_context_messages(
        messages, app_settings=settings, chars_per_token=4, input_budget_tokens=100000
    )
    assert len(output) == len(messages)
    assert "PRIVATE_PROBE_TEXT_CANARY" not in str(stats)
    assert stats["hybrid_shadow_candidate_tokens"] == stats["original_tokens"]
    assert stats["hybrid_shadow_source_verified"] is False
