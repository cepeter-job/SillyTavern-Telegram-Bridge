"""Synthetic continuity contracts do not constitute human narrative approval."""

import copy
from dataclasses import replace

import pytest

import tools.hybrid_context_fixture as fixtures
from bridge.context_hybrid_types import HISTORY_MARKER
from tools.extractive_context import evaluate_extractive_context


@pytest.mark.parametrize(
    "evidence",
    [
        "Mira placed the turquoise astrolabe in the copper case.",
        "I promise to return the copper key at dawn.",
        "Rowan did not give permission to enter the vault.",
        "We used the south ferry because the northern bridge collapsed.",
        "Rowan does not know the password; only Mira knows it.",
        "Aku berjanji tidak membuka gerbang karena Rowan belum tahu rahasianya.",
    ],
    ids=["query-fact", "promise", "negation", "cause", "knowledge", "indonesian"],
)
def test_anchored_evidence_and_local_referents_survive_preview(tmp_path, monkeypatch, evidence):
    before = "A folded map rests on the desk."
    after = "The lantern flickers above the map."
    passage = f"{before} {evidence} {after}"
    monkeypatch.setitem(fixtures.ANCHORS, 5, passage)
    db, scope, baseline, _ = fixtures.native_fixture(tmp_path)
    try:
        original = copy.deepcopy(baseline)
        changes = db.total_changes
        result = evaluate_extractive_context(db, scope, baseline, query="turquoise astrolabe")
        assert result.metrics["candidate_status"] == "preview"
        projected = next(m for m in result.candidate_messages if m.get(HISTORY_MARKER) == 5)
        assert before in projected["content"]
        assert evidence in projected["content"]
        assert after in projected["content"]
        assert result.dispatch_messages == baseline == original
        assert db.total_changes == changes
        assert "PRIVATE_HYBRID_CANARY" not in str(result.candidate_messages)
        assert result.metrics["semantic_continuity_proven"] is False
        assert result.metrics["production_activation_allowed"] is False
    finally:
        db.close()


@pytest.mark.parametrize("change", ["source", "incarnation", "summary", "reader"])
def test_late_revocation_discards_preview(tmp_path, change):
    db, scope, baseline, rows = fixtures.native_fixture(tmp_path)
    calls = 0

    def current_scope():
        nonlocal calls
        calls += 1
        if calls == 2:
            if change == "reader":
                return replace(scope, principals=("Mira",))
            if change == "source":
                db.execute("UPDATE messages SET content='Another branch' WHERE id=?", (rows[5][0],))
            elif change == "incarnation":
                db.execute("UPDATE sessions SET created_at=1000 WHERE chat_id='c' AND session_id='s'")
            else:
                db.execute("UPDATE memory_layer_state SET invalidated_from_id=1 WHERE layer='summary'")
            db.commit()
        return scope

    try:
        result = evaluate_extractive_context(
            db, scope, baseline, query="turquoise astrolabe", resolve_current_scope=current_scope
        )
        assert calls == 2
        assert result.metrics["candidate_status"] == "fallback"
        assert result.metrics["native_source_verified"] is False
        assert result.candidate_messages == result.dispatch_messages == baseline
        assert result.metrics["estimated_reduction_fraction"] == 0
    finally:
        db.close()


def test_unanchored_fact_is_not_misrepresented_as_semantically_preserved(tmp_path, monkeypatch):
    fact = "Mira moved the brass key into the blue drawer beside the western window."
    monkeypatch.setitem(fixtures.ANCHORS, 5, fact)
    db, scope, baseline, _ = fixtures.native_fixture(tmp_path)
    try:
        result = evaluate_extractive_context(db, scope, baseline, query="turquoise astrolabe")
        assert result.metrics["candidate_status"] == "preview"
        projected = next(m for m in result.candidate_messages if m.get(HISTORY_MARKER) == 5)
        assert fact not in projected["content"]
        assert fact in next(m for m in result.dispatch_messages if m.get(HISTORY_MARKER) == 5)["content"]
        assert result.dispatch_messages == baseline
        assert result.metrics["native_source_verified"] is True
        assert result.metrics["all_source_evidence_preserved"] is False
        assert result.metrics["semantic_continuity_proven"] is False
        assert result.metrics["production_activation_allowed"] is False
    finally:
        db.close()
