"""Content-free category accounting at the builder and persistence boundaries."""

import json

import pytest
from settings_test_support import make_test_settings
from test_story_memory_scope import db as db

from bridge.context_diagnostics import (
    context_diagnostics_snapshot,
    context_stats_key,
    record_context_attempts,
    save_context_stats,
)
from bridge.metadata import get_meta, set_meta


def test_section_estimates_aggregate_category_characters_without_retaining_text():
    from bridge.context_section_metrics import estimate_context_sections

    result = estimate_context_sections(
        {"mandatory": ["abc", "def"], "history": "hello", "world_info": "", "task": ["я"]},
        chars_per_token=4.0,
    )
    assert result == {"mandatory": 2, "history": 2, "world_info": 0, "derived": 0, "task": 1}
    assert "hello" not in json.dumps(result)


@pytest.mark.parametrize("ratio", [float("nan"), float("inf"), 0, 20, "private-provider-label"])
def test_section_estimates_use_safe_ratio_fallback(ratio):
    from bridge.context_section_metrics import estimate_context_sections

    assert estimate_context_sections({"mandatory": "12345"}, chars_per_token=ratio)["mandatory"] == 2


@pytest.mark.parametrize("sections", [{"private-source-id": "secret"}, {"task": ["safe", {"text": "secret"}]}])
def test_section_estimates_reject_unclassified_or_nontext_inputs(sections):
    from bridge.context_section_metrics import estimate_context_sections

    with pytest.raises((TypeError, ValueError)):
        estimate_context_sections(sections)


def test_saved_sections_are_allowlisted_and_reject_invalid_counts(db):
    save_context_stats(
        db,
        "c",
        "s",
        {
            "section_estimated_tokens": {
                "mandatory": 12,
                "history": True,
                "world_info": -1,
                "derived": "private memory text",
                "task": 2.5,
                "source-id-secret": 9,
                "model": "private-model",
            },
            "original_tokens": "private prompt",
            "final_tokens": False,
            "memory_trimmed": "private memory",
            "source": ["private provider"],
            "input_budget_limiter": {"private": "label"},
            "chars_per_token": float("nan"),
        },
    )
    saved = json.loads(get_meta(db, context_stats_key("c", "s")))
    assert saved == {"section_estimated_tokens": {"mandatory": 12}}


def test_transport_observations_preserve_builder_estimates_even_on_failure(db):
    save_context_stats(db, "c", "s", {"section_estimated_tokens": {"mandatory": 12, "task": 5}})
    with pytest.raises(RuntimeError), record_context_attempts(db, "c", "s") as observe:
        observe({"final_tokens": 99, "section_estimated_tokens": {"mandatory": 999}})
        observe({"final_tokens": 100, "estimated": True})
        raise RuntimeError("synthetic generation failure")
    saved = json.loads(get_meta(db, context_stats_key("c", "s")))
    assert saved["section_estimated_tokens"] == {"mandatory": 12, "task": 5}
    assert saved["final_tokens"] == 100


def test_snapshot_redacts_legacy_saved_nested_metrics_and_invalid_metadata(db, tmp_path):
    set_meta(
        db,
        context_stats_key("c", "s"),
        json.dumps(
            {
                "section_estimated_tokens": {"mandatory": 12, "task": 2**64, "raw": "private prompt"},
                "source": ["private"],
                "input_budget_limiter": ["private"],
            }
        ),
    )
    snapshot = context_diagnostics_snapshot(
        db, "c", {"session_id": "s", "model_id": "synthetic"}, app_settings=make_test_settings(home=tmp_path)
    )
    assert snapshot["section_estimated_tokens"] == {"mandatory": 12}
    assert "private" not in json.dumps(snapshot)


@pytest.mark.parametrize("invalid", [None, [], "private prompt", True, {"task": 2**64}])
def test_saved_section_metric_shape_is_validated(db, invalid):
    save_context_stats(db, "c", "s", {"section_estimated_tokens": invalid})
    assert json.loads(get_meta(db, context_stats_key("c", "s"))) == {}


def test_selection_metrics_allow_only_fixed_enums_counts_and_flags(db):
    selection = {
        "mode": "shadow",
        "reason": "selected",
        "original_tokens": 25,
        "candidate_tokens": 20,
        "selected_blocks": 2,
        "deduplicated_blocks": 1,
        "coverage_valid": True,
        "applied": False,
    }
    save_context_stats(db, "c", "s", {"selection_metrics": {**selection, "source_ids": ["private-source"]}})
    with record_context_attempts(db, "c", "s") as observe:
        observe({"final_tokens": 99, "selection_metrics": {"mode": "enabled", "reason": "private"}})
    saved = json.loads(get_meta(db, context_stats_key("c", "s")))
    assert saved["selection_metrics"] == selection


def test_selection_metrics_reject_private_strings_and_invalid_values(db):
    save_context_stats(
        db,
        "c",
        "s",
        {
            "selection_metrics": {
                "mode": "private source",
                "reason": ["private reason"],
                "original_tokens": -1,
                "candidate_tokens": True,
                "selected_blocks": 1.5,
                "deduplicated_blocks": "private block",
                "coverage_valid": 1,
                "applied": "yes",
            }
        },
    )
    assert json.loads(get_meta(db, context_stats_key("c", "s"))) == {}


def test_saved_ratio_rejects_arbitrarily_large_json_integer(db):
    save_context_stats(db, "c", "s", {"chars_per_token": 10**1000, "section_estimated_tokens": {"task": 0}})
    assert json.loads(get_meta(db, context_stats_key("c", "s"))) == {"section_estimated_tokens": {"task": 0}}
