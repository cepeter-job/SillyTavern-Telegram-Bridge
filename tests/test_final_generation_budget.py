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
from bridge.context_diagnostics import context_diagnostics_snapshot, context_stats_key, save_context_stats
from bridge.light_novel_format import add_inline_contract
from bridge.metadata import get_meta
from bridge.prompt_panels import prompt_panel_text
from bridge.settings import ConfigurationError, load_app_settings


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


def test_direct_builder_over_cap_error_reports_input_cap(tmp_path, monkeypatch):
    settings = settings_for(tmp_path, 1_000_000)
    monkeypatch.setattr(generation, "build_system_prompt", lambda *a, **k: "FIXED " + "x" * 210_000)
    persona = SimpleNamespace(name=lambda *a: pytest.fail("unexpected persona read"), get=lambda *a: None)
    stats = {}

    with pytest.raises(ContextWindowBudgetError) as exc:
        generation.build_chat_messages(
            {"session_id": "s", "model_id": "synthetic", "persona_id": "", "world_file": ""},
            {"name": "Synthetic", "first_mes": "", "post_history_instructions": ""},
            "CURRENT",
            [],
            persona_service=persona,
            app_settings=settings,
            context_stats=stats,
        )

    assert exc.value.stats["window_tokens"] == 1_000_000
    assert exc.value.stats["input_cap_tokens"] == 49_152
    assert exc.value.stats["input_budget_limiter"] == "input-cap"
    assert stats["input_cap_tokens"] == 49_152
    assert "configured input cap" in str(exc.value).lower()


def test_default_input_cap_compacts_large_window_before_story_dispatch(db, tmp_path):
    settings = settings_for(tmp_path, 1_000_000)
    messages = [{"role": "system", "content": "fixed"}]
    messages.extend({"role": "user" if index % 2 == 0 else "assistant", "content": "x" * 12000} for index in range(45))
    messages.append({"role": "user", "content": "CURRENT TURN"})

    final = finalize(db, settings, messages, output=4000)
    saved = json.loads(get_meta(db, context_stats_key("c", "s"), ""))

    assert len(final) < len(messages)
    assert final[0]["content"] == "fixed" and final[-1]["content"] == "CURRENT TURN"
    assert estimate_message_tokens(final) <= 49_152
    assert saved["window_tokens"] == 1_000_000
    assert saved["input_cap_tokens"] == 49_152
    assert saved["input_budget_limiter"] == "input-cap"
    assert saved["budget_tokens"] == 49_152
    assert saved["dropped_history"] > 0


def test_physical_window_and_requested_output_can_limit_below_input_cap(tmp_path):
    profile = context_profile("synthetic", requested_output_tokens=10_000, app_settings=settings_for(tmp_path, 55_000))
    assert profile.window_tokens == 55_000
    assert profile.output_reserve_tokens == 10_000
    assert profile.input_cap_tokens == 49_152
    assert profile.input_budget_tokens == 43_900
    assert profile.input_budget_limiter == "model-window"


def test_overlarge_current_turn_reports_input_cap_without_clipping(db, tmp_path):
    current = "CURRENT " + "x" * 210_000
    messages = [{"role": "system", "content": "fixed"}, {"role": "user", "content": current}]
    with pytest.raises(ContextWindowBudgetError) as exc:
        finalize(db, settings_for(tmp_path, 1_000_000), messages, output=4000)
    assert exc.value.stats["input_cap_tokens"] == 49_152
    assert exc.value.stats["budget_tokens"] == 49_152
    assert "input cap" in str(exc.value).lower()
    assert "49,152" in str(exc.value)
    assert messages[-1]["content"] == current


@pytest.mark.parametrize("value", ["0", "1023", "1000001", "abc"])
def test_input_cap_setting_rejects_invalid_values(tmp_path, value):
    with pytest.raises(ConfigurationError):
        load_app_settings({"SILLYTAVERN_CONTEXT_INPUT_CAP_TOKENS": value}, home=tmp_path)


def test_input_cap_setting_accepts_lower_cost_alternative(tmp_path):
    settings = load_app_settings(
        {"SILLYTAVERN_CONTEXT_WINDOW_TOKENS": "1000000", "SILLYTAVERN_CONTEXT_INPUT_CAP_TOKENS": "32768"},
        home=tmp_path,
    )
    profile = context_profile("synthetic", requested_output_tokens=4000, app_settings=settings)
    assert settings.context_input_cap_tokens == 32_768
    assert profile.input_budget_tokens == 32_768


def test_prompt_budget_shows_true_window_and_effective_input_cap(db, tmp_path):
    text = prompt_panel_text(
        db,
        "c",
        {"session_id": "s", "model_id": "synthetic"},
        {"name": "Synthetic"},
        section="budget",
        group_service=None,
        memory_service=None,
        app_settings=settings_for(tmp_path, 1_000_000),
    )
    assert "Context window: 1000000 tokens" in text
    assert "Configured input cap: 49152 tokens" in text
    assert "Effective input budget: ~49152 tokens (input cap)" in text


def test_prompt_budget_distinguishes_current_cap_from_saved_request(db, tmp_path):
    old_settings = settings_for(tmp_path, 1_000_000)
    session = {"session_id": "s", "model_id": "synthetic"}
    save_context_stats(
        db,
        "c",
        "s",
        {
            "window_tokens": 1_000_000,
            "output_reserve_tokens": 4_000,
            "safety_margin_tokens": 8_192,
            "budget_tokens": 49_152,
            "input_cap_tokens": old_settings.context_input_cap_tokens,
            "input_budget_limiter": "input-cap",
            "final_tokens": 48_000,
        },
    )
    current_settings = make_test_settings(base=old_settings, context_input_cap_tokens=32_768)

    snapshot = context_diagnostics_snapshot(db, "c", session, app_settings=current_settings)
    text = prompt_panel_text(
        db,
        "c",
        session,
        {"name": "Synthetic"},
        section="budget",
        group_service=None,
        memory_service=None,
        app_settings=current_settings,
    )

    assert snapshot["configured_input_cap_tokens"] == 32_768
    assert snapshot["input_cap_tokens"] == 49_152
    assert snapshot["budget_tokens"] == 49_152
    assert snapshot["input_budget_limiter"] == "input-cap"
    assert "Configured input cap: 32768 tokens" in text
    assert "Request input cap: 49152 tokens" in text
    assert "Last request input budget: ~49152 tokens (input cap)" in text
    assert "Last assembled prompt: 48000 / 49152 estimated tokens" in text
