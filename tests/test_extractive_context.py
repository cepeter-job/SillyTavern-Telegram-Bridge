"""An offline extractive preview must not become a production pruning grant."""

import copy
import importlib.util

import pytest

from bridge.context_hybrid_types import HISTORY_MARKER, SHADOW_MARKER
from bridge.context_selection_runtime import choose_context_messages
from bridge.settings import load_app_settings
from tools.hybrid_context_fixture import native_fixture


def evaluator():
    assert importlib.util.find_spec("tools.extractive_context") is not None, "extractive evaluator not implemented"
    from tools.extractive_context import evaluate_extractive_context

    return evaluate_extractive_context


@pytest.fixture
def story(tmp_path):
    db, scope, messages, rows = native_fixture(tmp_path, variant="commitment_dense")
    yield db, scope, messages, rows
    db.close()


def test_shorter_candidate_preserves_roles_all_users_and_fixed_payloads(story):
    db, scope, baseline, rows = story
    original = copy.deepcopy(baseline)
    result = evaluator()(db, scope, baseline, query="turquoise astrolabe")
    assert result.dispatch_messages == baseline == original
    assert result.metrics["candidate_status"] == "preview"
    assert result.metrics["candidate_tokens"] < result.metrics["baseline_tokens"]
    assert [m["role"] for m in result.candidate_messages] == [m["role"] for m in baseline]
    assert len(result.candidate_messages) == len(baseline)
    for before, after in zip(baseline, result.candidate_messages, strict=True):
        i = before.get(HISTORY_MARKER)
        if i is None or before["role"] == "user" or i == 0 or i >= len(rows) - 8:
            assert after == before
    assert result.metrics["native_source_verified"] is True
    assert result.metrics["all_source_evidence_preserved"] is False
    assert result.metrics["semantic_continuity_proven"] is False
    assert result.metrics["production_activation_allowed"] is False
    assert result.metrics["archive_blocks_added"] == 0
    assert "PRIVATE_HYBRID_CANARY" not in str(result.candidate_messages)


@pytest.mark.parametrize("mode", ["off", "shadow", "enabled"])
def test_candidate_cannot_be_dispatched_in_any_mode(story, tmp_path, mode):
    db, scope, baseline, _ = story
    result = evaluator()(db, scope, baseline, query="astrolabe")
    assert any(m.get(SHADOW_MARKER) for m in result.candidate_messages)
    settings = load_app_settings({"SILLYTAVERN_CONTEXT_SELECTION_MODE": mode}, home=tmp_path)
    with pytest.raises(ValueError, match="shadow-only"):
        choose_context_messages(
            result.candidate_messages, app_settings=settings, chars_per_token=4, input_budget_tokens=100000
        )


@pytest.mark.parametrize("damage", ["reader", "source", "summary", "archive"])
def test_unsafe_source_never_produces_a_candidate(story, damage):
    db, scope, baseline, rows = story
    options = {}
    if damage == "reader":
        options["resolve_current_scope"] = lambda: None
    elif damage == "source":
        db.execute("UPDATE messages SET content='Other branch' WHERE id=?", (rows[4][0],))
    elif damage == "summary":
        db.execute("UPDATE memory_layer_state SET invalidated_from_id=1 WHERE layer='summary'")
    else:
        db.execute("DELETE FROM memory_layer_checkpoints WHERE layer='summary'")
    db.commit()
    result = evaluator()(db, scope, baseline, query="astrolabe", **options)
    assert result.candidate_messages == result.dispatch_messages == baseline
    assert result.metrics["candidate_status"] == "fallback"
    assert result.metrics["estimated_reduction_fraction"] == 0


@pytest.mark.parametrize("variant,count", [("short_history", 8), ("unsupported_script", 44)])
def test_negative_controls_remain_in_denominator(tmp_path, variant, count):
    db, scope, baseline, _ = native_fixture(tmp_path, count=count, variant=variant)
    try:
        result = evaluator()(db, scope, baseline, query="astrolabe")
        assert result.candidate_messages == baseline
        assert result.metrics["estimated_reduction_fraction"] == 0
    finally:
        db.close()


def test_revalidates_reader_after_constructing_preview(story):
    db, scope, baseline, _ = story
    scopes = iter((scope, None))
    result = evaluator()(db, scope, baseline, query="astrolabe", resolve_current_scope=lambda: next(scopes))
    assert result.candidate_messages == baseline
    assert result.metrics["native_source_verified"] is False


def test_report_never_exports_transcript_or_scope(story):
    db, scope, baseline, _ = story
    before = db.total_changes
    result = evaluator()(db, scope, baseline, query="astrolabe")
    assert db.total_changes == before
    assert "I promise" not in str(result.metrics)
    assert "PRIVATE_HYBRID_CANARY" not in str(result.metrics)
    assert not {"chat_id", "session_id", "source_sha256", "reader"}.intersection(result.metrics)


@pytest.mark.parametrize("protected", ['"The violet lantern glows."', "  The <scene> remains misty.  "])
def test_quote_or_unparseable_source_is_never_silently_discarded(tmp_path, monkeypatch, protected):
    import tools.hybrid_context_fixture as fixtures

    monkeypatch.setitem(fixtures.ANCHORS, 5, protected)
    db, scope, baseline, _ = fixtures.native_fixture(tmp_path)
    try:
        result = evaluator()(db, scope, baseline, query="astrolabe")
        original = next(m for m in baseline if m.get(HISTORY_MARKER) == 5)
        projected = next(m for m in result.candidate_messages if m.get(HISTORY_MARKER) == 5)
        assert projected == original
    finally:
        db.close()


def test_nothing_imports_preview_into_production_runtime():
    from pathlib import Path

    for path in (Path(__file__).parents[1] / "bridge").glob("*.py"):
        assert "tools.extractive_context" not in path.read_text(encoding="utf-8")
