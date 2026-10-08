"""History shadow previews have one baseline dispatch and no changed provider roles."""

from dataclasses import replace

import pytest
from settings_test_support import make_test_settings

from bridge.context_compaction import estimate_message_tokens
from bridge.context_selection_runtime import ContextSelectionStaleError, choose_context_messages
from bridge.memory_contracts import MemoryPromptContext, MemoryReadScope


def prepare_messages(*, status="", wrong_source=False):
    source = tuple((i + 1, "user" if i % 2 == 0 else "assistant", f"Routine {i}: inspected.") for i in range(44))
    history = [
        {"role": role, "content": content, "_context_history_index": index}
        for index, (_rowid, role, content) in enumerate(source)
    ]
    context = MemoryPromptContext(
        scope=MemoryReadScope("chat", "session", 1.0, 44, 0, ("mira",)),
        selection_mode="shadow",
        selection_reason="ambiguous",
        selection_coverage_valid=True,
        selection_guard=lambda: status,
        selection_history_source_rows=source[:-1] if wrong_source else source,
        selection_query="Inspect the window.",
    )
    return [
        {"role": "system", "content": "Preserve character and user agency.", "_context_selection": context},
        *history,
        {"role": "user", "content": "Inspect the window."},
    ]


def run(messages, *, mode="shadow", slices="history"):
    settings = make_test_settings(
        environ={
            "SILLYTAVERN_CONTEXT_SELECTION_MODE": mode,
            "SILLYTAVERN_CONTEXT_SELECTION_SLICES": slices,
        }
    )
    return choose_context_messages(messages, app_settings=settings, chars_per_token=4.0, input_budget_tokens=8000)


def stripped(messages):
    return [
        {k: v for k, v in message.items() if k not in {"_context_selection", "_context_history_index"}}
        for message in messages
    ]


def test_shadow_sends_one_byte_identical_baseline_with_bounded_preview_metrics():
    messages = prepare_messages()
    expected = stripped(messages)
    baseline_tokens = estimate_message_tokens(expected)
    result, metrics = run(messages)
    assert result == expected
    assert metrics["mode"] == "shadow"
    assert metrics["applied"] is False
    assert metrics["history_shadow_reason"] == "selected"
    assert metrics["history_shadow_reframed_turns"] >= 9
    assert 0 < metrics["history_shadow_candidate_tokens"] < baseline_tokens
    assert messages[0]["_context_selection"].selection_guard() == ""


def test_history_slice_cannot_activate_a_role_reframed_candidate():
    messages = prepare_messages()
    context = messages[0]["_context_selection"]
    messages[0]["_context_selection"] = replace(context, selection_mode="enabled")
    result, metrics = run(messages, mode="enabled")
    assert result == stripped(messages)
    assert metrics["applied"] is False
    assert metrics["reason"] == "not_approved"
    assert "history_shadow_candidate_tokens" not in metrics


@pytest.mark.parametrize("reason", ["source_changed", "invalid_scope"])
def test_revoked_source_aborts_before_dispatch_even_in_shadow(reason):
    with pytest.raises(ContextSelectionStaleError):
        run(prepare_messages(status=reason))


def test_source_mismatch_is_not_a_history_savings_claim():
    result, metrics = run(prepare_messages(wrong_source=True))
    assert len(result) == 46
    assert metrics["history_shadow_reason"] == "ambiguous"
    assert metrics["history_shadow_reframed_turns"] == 0


def test_off_mode_strips_internal_source_markers_without_changing_text():
    messages = prepare_messages()
    result, metrics = run(messages, mode="off")
    assert result == stripped(messages)
    assert metrics["reason"] == "off"
    assert "history_shadow_candidate_tokens" not in metrics
