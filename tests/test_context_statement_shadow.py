"""Source-backed statement previews are not production-dispatchable prompts."""

import copy
import json
from dataclasses import replace

import pytest

from bridge.context_hybrid_shadow import evaluate_hybrid_shadow
from bridge.context_hybrid_types import SHADOW_MARKER
from bridge.context_selection_runtime import choose_context_messages
from bridge.context_statement_shadow import STATEMENT_MARKER, evaluate_statement_shadow
from bridge.memory_scope_store import resolve_memory_scope
from bridge.settings import load_app_settings
from tools.hybrid_context_fixture import native_fixture


@pytest.fixture
def story(tmp_path):
    result = native_fixture(tmp_path, variant="commitment_dense")
    yield result
    result[0].close()


def test_dense_commitments_reduce_exact_statements_but_not_original_dispatch(story):
    db, scope, baseline, rows = story
    old = evaluate_hybrid_shadow(db, scope, baseline, query="turquoise astrolabe")
    assert old.metrics["estimated_reduction_fraction"] == 0
    before = copy.deepcopy(baseline)
    result = evaluate_statement_shadow(db, scope, baseline, query="turquoise astrolabe")
    assert baseline == before == result.dispatch_messages
    assert result.metrics["candidate_status"] == "preview"
    assert result.metrics["candidate_tokens"] < result.metrics["baseline_tokens"]
    assert result.metrics["source_statement_receipts_verified"] is True
    assert result.metrics["semantic_continuity_proven"] is False
    assert result.metrics["production_activation_allowed"] is False
    packets = [item for item in result.candidate_messages if item.get(STATEMENT_MARKER)]
    assert len(packets) == 1 and packets[0][SHADOW_MARKER] is True
    packet = json.loads(packets[0]["content"].split("\n", 1)[1])
    assert packet["reference_only"] is True
    assert packet["source_statements"]
    seen = [item["turn"] for item in packet["source_statements"]]
    assert seen == sorted(set(seen))
    for item in packet["source_statements"]:
        row = rows[item["turn"]]
        assert item["role"] == row[1]
        for begin, end, text in item["spans"]:
            assert row[2][begin:end] == text
    all_text = json.dumps(result.candidate_messages, ensure_ascii=False)
    for index in range(len(rows) - 8):
        if "I promise to preserve" in rows[index][2]:
            assert f"I promise to preserve item {index}" in all_text
    assert "PRIVATE_HYBRID_CANARY" not in all_text
    assert result.candidate_messages[0] == baseline[0]
    assert result.candidate_messages[-1] == baseline[-1]


def test_reader_specific_summary_does_not_widen_private_knowledge(story):
    db, scope, baseline, _ = story
    mira = resolve_memory_scope(db, "c", {"session_id": "s"}, {"name": "Mira"})
    own = evaluate_statement_shadow(db, mira, baseline, query="astrolabe")
    unrelated = evaluate_statement_shadow(db, scope, baseline, query="astrolabe")
    assert own.metrics["candidate_status"] == "preview"
    assert unrelated.metrics["candidate_status"] == "preview"
    assert "PRIVATE_HYBRID_CANARY" in json.dumps(own.candidate_messages)
    assert "PRIVATE_HYBRID_CANARY" not in json.dumps(unrelated.candidate_messages)
    assert "PRIVATE_HYBRID_CANARY" not in json.dumps(own.metrics)


@pytest.mark.parametrize("change", ["foreign", "stale", "rewritten", "gap", "unsupported"])
def test_revoked_or_unverifiable_source_falls_back_to_full_baseline(story, change):
    db, scope, baseline, rows = story
    if change == "foreign":
        scope = replace(scope, session_id="other")
    elif change == "stale":
        scope = replace(scope, session_created_at=99.0)
    elif change == "rewritten":
        db.execute("UPDATE messages SET content='Changed denial.' WHERE id=?", (rows[10][0],))
    elif change == "gap":
        db.execute("DELETE FROM memory_segments WHERE start_id=? AND layer='summary'", (rows[11][0],))
    else:
        db.execute("UPDATE messages SET content='秘密を守る約束です。' WHERE id=?", (rows[15][0],))
    db.commit()
    result = evaluate_statement_shadow(db, scope, baseline, query="astrolabe")
    assert result.dispatch_messages == result.candidate_messages == baseline
    assert result.metrics["candidate_status"] == "fallback"
    assert result.metrics["estimated_reduction_fraction"] == 0


def test_mandatory_card_media_and_current_user_turn_remain_verbatim(story):
    db, scope, baseline, _ = story
    baseline[0]["content"] *= 1200
    baseline[-1]["content"] = [
        {"type": "text", "text": "Inspect the astrolabe."},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,synthetic"}},
    ]
    result = evaluate_statement_shadow(db, scope, baseline, query="astrolabe")
    assert result.dispatch_messages == baseline
    assert result.candidate_messages[0] == baseline[0]
    assert result.candidate_messages[-1] == baseline[-1]
    assert result.metrics["production_activation_allowed"] is False


@pytest.mark.parametrize("mode", ["off", "shadow", "enabled"])
def test_statement_candidate_rejected_by_dispatch_in_every_mode(story, tmp_path, mode):
    db, scope, baseline, _ = story
    packet = evaluate_statement_shadow(db, scope, baseline, query="astrolabe")
    assert packet.metrics["candidate_status"] == "preview"
    settings = load_app_settings({"SILLYTAVERN_CONTEXT_SELECTION_MODE": mode}, home=tmp_path)
    with pytest.raises(ValueError, match="shadow-only"):
        choose_context_messages(packet.candidate_messages, app_settings=settings,
                                chars_per_token=4, input_budget_tokens=100000)


def test_short_history_never_claims_savings(tmp_path):
    db, scope, baseline, _ = native_fixture(tmp_path, count=8)
    try:
        result = evaluate_statement_shadow(db, scope, baseline, query="astrolabe")
        assert result.candidate_messages == result.dispatch_messages == baseline
        assert result.metrics["estimated_reduction_fraction"] == 0
    finally:
        db.close()
