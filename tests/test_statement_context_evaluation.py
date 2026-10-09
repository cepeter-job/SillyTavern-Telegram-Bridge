"""Full synthetic story-prompt token estimates must keep negative controls."""

import json

import pytest

from tools.evaluate_statement_context import (
    CODE_FILES,
    build_report,
    reconstruct_synthetic_prompt,
)


def test_frozen_six_case_report_preserves_controls_and_activation_off(tmp_path):
    report = build_report(tmp_path)
    assert report["type"] == "native_statement_shadow_complete_prompt"
    assert report["provider_requests"] == 0
    assert report["production_activation_allowed"] is False
    assert report["provider_measured_reduction"] is None
    assert report["semantic_continuity_proven"] is False
    cases = {item["case_id"]: item for item in report["cases"]}
    assert set(cases) == {
        "unique_dialogue",
        "commitment_dense",
        "indonesian",
        "unsupported_script",
        "short_history",
        "large_character_card",
    }
    assert cases["commitment_dense"]["old_hybrid"]["estimated_reduction_fraction"] == 0
    assert cases["commitment_dense"]["estimated_reduction_fraction"] > 0
    for name in ("unsupported_script", "short_history"):
        assert cases[name]["estimated_reduction_fraction"] == 0
    assert cases["large_character_card"]["fixed_prompt_tokens"] > cases["unique_dialogue"]["fixed_prompt_tokens"]
    assert (
        cases["large_character_card"]["estimated_reduction_fraction"]
        < cases["unique_dialogue"]["estimated_reduction_fraction"]
    )
    assert sum(item["weight"] for item in cases.values()) == pytest.approx(1.0)
    assert all(item["dispatch_uses_full_history"] for item in cases.values())
    assert "PRIVATE_HYBRID_CANARY" not in json.dumps(report)


def test_full_synthetic_prompt_contains_card_world_system_history_and_current_input(tmp_path):
    messages = reconstruct_synthetic_prompt(tmp_path)
    visible = "\n".join(str(item["content"]) for item in messages)
    assert "CHARACTER_CARD" in visible
    assert "World Scenario" in visible
    assert "Session continuity summary" in visible
    assert "Native session" in visible
    assert "POST_HISTORY_DIRECTIVE" in visible
    assert "Describe the turquoise astrolabe" in visible
    assert any("_context_history_index" in item for item in messages)
    assert any("_context_history_index" not in item and item["role"] == "system" for item in messages)


def test_machine_report_omits_story_text_and_identifying_scope(tmp_path):
    report = build_report(tmp_path)
    encoded = json.dumps(report, ensure_ascii=False)
    assert "PRIVATE_HYBRID_CANARY" not in encoded
    assert "I promise to preserve" not in encoded
    assert "CHARACTER_CARD" not in encoded
    assert "session_id" not in encoded


def test_digest_binds_runtime_policy_and_eval_implementation():
    assert {
        "bridge/context_selection_runtime.py",
        "bridge/context_hybrid_shadow.py",
        "bridge/context_statement_policy.py",
        "bridge/context_statement_shadow.py",
        "bridge/memory_contracts.py",
        "bridge/generation.py",
    }.issubset(CODE_FILES)
