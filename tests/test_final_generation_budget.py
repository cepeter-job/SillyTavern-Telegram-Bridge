"""Final request budgeting uses actual allocation and preserves mandatory text."""

import json
from types import SimpleNamespace

import pytest
from settings_test_support import make_test_settings
from test_story_memory_scope import db as db

from bridge import generation
from bridge.context_compaction import (
    ContextWindowBudgetError,
    compact_chat_messages,
    context_profile,
    estimate_message_tokens,
)
from bridge.context_diagnostics import context_stats_key
from bridge.light_novel_format import add_inline_contract
from bridge.metadata import get_meta


def settings_for(tmp_path, window=16384):
    return make_test_settings(home=tmp_path, context_window_tokens=window)


def finalize(db, settings, messages, output=1800, **kwargs):
    return generation.finalize_generation_messages(
        db,
        "c",
        {"session_id": "s", "model_id": "synthetic"},
        messages,
        {"max_tokens": output},
        app_settings=settings,
        **kwargs,
    )


def test_explicit_output_is_reserved_without_global_clamping(tmp_path):
    settings = settings_for(tmp_path)
    small = context_profile("synthetic", requested_output_tokens=1800, app_settings=settings)
    large = context_profile("synthetic", requested_output_tokens=12000, app_settings=settings)
    assert (small.input_budget_tokens, large.input_budget_tokens) == (14072, 3872)
    assert large.output_reserve_tokens == 12000


def test_impossible_output_allocation_is_rejected(tmp_path):
    with pytest.raises(ContextWindowBudgetError) as exc:
        context_profile("synthetic", requested_output_tokens=12000, app_settings=settings_for(tmp_path, 8000))
    assert exc.value.stats["output_reserve_tokens"] == 12000
    assert exc.value.stats["over_budget"] is True


def test_explicit_small_input_budget_is_not_raised_to_2048(tmp_path):
    settings = settings_for(tmp_path, 4096)
    profile = context_profile("synthetic", requested_output_tokens=3000, app_settings=settings)
    assert profile.input_budget_tokens == 584
    messages = [{"role": "system", "content": "fixed"}, {"role": "user", "content": "x" * 2600}]
    compacted, stats = compact_chat_messages(messages, budget_tokens=584, app_settings=settings)
    assert compacted == messages
    assert stats["budget_tokens"] == 584 and stats["over_budget"] is True


def test_ratio_1_5_drives_both_estimation_and_trim_size(tmp_path):
    messages = [
        {"role": "system", "content": "fixed"},
        {"role": "user", "content": "<untrusted_memory>\n" + "m" * 6000 + "\n</untrusted_memory>\nCURRENT"},
    ]
    compacted, stats = compact_chat_messages(
        messages, budget_tokens=3200, chars_per_token=1.5, app_settings=settings_for(tmp_path)
    )
    text = compacted[-1]["content"]
    assert estimate_message_tokens(compacted, chars_per_token=1.5) <= 3200
    assert text.count("m") >= 4000, "The configured ratio must not over-trim using a hard-coded factor of four."
    assert stats["memory_trimmed"] is True


def test_novel_contract_is_budgeted_and_failed_final_stats_are_persisted(db, tmp_path):
    settings = settings_for(tmp_path, 4096)
    original = [{"role": "system", "content": "FIXED " + "x" * 6200}, {"role": "user", "content": "CURRENT"}]
    assert estimate_message_tokens(original) < 1784
    final = add_inline_contract(original, 4, "auto")
    assert estimate_message_tokens(final) > 1784
    with pytest.raises(ContextWindowBudgetError):
        finalize(db, settings, final)
    saved = json.loads(get_meta(db, context_stats_key("c", "s"), ""))
    assert saved["final_tokens"] == estimate_message_tokens(final)
    assert saved["requested_output_tokens"] == 1800
    assert saved["output_reserve_tokens"] == 1800
    assert saved["over_budget"] is True
    assert "FIXED" not in json.dumps(saved) and "CURRENT" not in json.dumps(saved)


def test_continuation_target_is_protected_even_before_a_newer_user(db, tmp_path):
    target = "EXACT ENDING " + "A" * 8000
    messages = [
        {"role": "system", "content": "fixed"},
        {"role": "assistant", "content": target},
        {"role": "user", "content": "later user"},
        {"role": "user", "content": "Continue from the exact ending."},
    ]
    with pytest.raises(ContextWindowBudgetError):
        finalize(db, settings_for(tmp_path, 4096), messages, preserve_last_assistant=True)
    assert messages[1]["content"] == target


def test_multimodal_content_survives_finalization_byte_for_byte(db, tmp_path):
    uri = "data:image/jpeg;base64," + "A" * 500000
    messages = [
        {"role": "system", "content": "fixed"},
        {
            "role": "user",
            "content": [{"type": "text", "text": "CURRENT"}, {"type": "image_url", "image_url": {"url": uri}}],
        },
    ]
    final = finalize(db, settings_for(tmp_path, 4096), messages)
    assert final == messages
    assert estimate_message_tokens(final) < 1100


@pytest.mark.parametrize("protected", ["current", "fixed", "scene"])
def test_actual_builder_marks_only_real_optional_sections(db, tmp_path, monkeypatch, protected):
    settings = settings_for(tmp_path, 4096)
    lookalike = "<untrusted_memory>\n" + "x" * 8000 + "\n</untrusted_memory>"
    fixed = "FIXED"
    current = "CURRENT"
    scene = ""
    if protected == "current":
        current = lookalike
    elif protected == "fixed":
        fixed = "## Session continuity summary\n" + "x" * 8000
    else:
        # The actual bounded scene block remains mandatory when optional memory is exhausted.
        scene = "SCENE " + "x" * 4994
        current += "y" * 2500
    monkeypatch.setattr(generation, "build_system_prompt", lambda *a, **k: fixed)
    persona = SimpleNamespace(name=lambda *a: pytest.fail("unexpected persona read"), get=lambda *a: None)
    messages = generation.build_chat_messages(
        {"session_id": "s", "model_id": "synthetic", "persona_id": "", "world_file": ""},
        {"name": "Synthetic", "first_mes": "", "post_history_instructions": ""},
        current,
        [],
        persona_service=persona,
        scene_context=scene,
        memory_context="optional " * 1000,
        app_settings=settings,
        defer_compaction=True,
    )
    with pytest.raises(ContextWindowBudgetError):
        finalize(db, settings, messages)
