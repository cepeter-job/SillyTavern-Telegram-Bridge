"""Hybrid proposals are non-dispatchable, conservative and preserve protected spans."""

import copy
import json
from contextlib import closing
from dataclasses import replace

import pytest

from bridge.context_hybrid_shadow import evaluate_hybrid_shadow
from bridge.context_hybrid_types import SHADOW_MARKER, HybridOptions
from bridge.memory_scope_store import resolve_memory_scope
from tools.hybrid_context_fixture import ANCHORS, MARKER, native_fixture


@pytest.fixture
def story(tmp_path):
    value = native_fixture(tmp_path)
    yield value
    value[0].close()


def test_unique_dialogue_hybrid_keeps_tail_critical_facts_and_retrieved_context(story):
    db, scope, baseline, rows = story
    before = copy.deepcopy(baseline)
    result = evaluate_hybrid_shadow(db, scope, baseline, query="turquoise astrolabe")
    assert baseline == before == result.dispatch_messages
    assert result.metrics["candidate_status"] == "preview"
    assert result.metrics["candidate_tokens"] < result.metrics["baseline_tokens"]
    assert result.metrics["native_source_verified"] is True
    assert result.metrics["semantic_continuity_proven"] is False
    assert result.metrics["production_activation_allowed"] is False
    kept = {m[MARKER]: m for m in result.candidate_messages if MARKER in m}
    for index in (0, *ANCHORS, 27, *range(len(rows) - 8, len(rows))):
        assert kept[index] == baseline[index + 1]

    def nonhistory(messages):
        return [m for m in messages if MARKER not in m and SHADOW_MARKER not in m]

    assert nonhistory(result.candidate_messages) == nonhistory(baseline)
    assert "PRIVATE_HYBRID_CANARY" not in json.dumps(result.candidate_messages)
    assert "closed before dawn" in json.dumps(result.candidate_messages)
    assert any(m.get(SHADOW_MARKER) is True and m["role"] == "user" for m in result.candidate_messages)
    assert result.metrics["omitted_turns"] > 0


def test_authorized_private_summary_is_included_without_granting_other_readers(story):
    db, scope, messages, _ = story
    mira = resolve_memory_scope(db, "c", {"session_id": "s"}, {"name": "Mira"})
    own = evaluate_hybrid_shadow(db, mira, messages, query="astrolabe")
    other = evaluate_hybrid_shadow(db, scope, messages, query="astrolabe")
    assert "PRIVATE_HYBRID_CANARY" in json.dumps(own.candidate_messages)
    assert "PRIVATE_HYBRID_CANARY" not in json.dumps(other.candidate_messages)
    assert "PRIVATE_HYBRID_CANARY" not in json.dumps(own.metrics)
    assert own.dispatch_messages == other.dispatch_messages == messages


def test_large_card_and_multimodal_nonhistory_are_never_capped_or_rewritten(story):
    db, scope, messages, _ = story
    messages[0]["content"] = "CARD_WORLD_REQUIREMENT " * 4000
    messages[-1]["content"] = [
        {"type": "text", "text": "Inspect the astrolabe."},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,synthetic"}},
    ]
    result = evaluate_hybrid_shadow(db, scope, messages, query="astrolabe")
    assert result.candidate_messages[0] == messages[0]
    assert result.candidate_messages[-1] == messages[-1]
    assert result.dispatch_messages == messages
    assert result.metrics["production_activation_allowed"] is False


def test_extended_recent_tail_and_continuation_remain_verbatim(story):
    db, scope, messages, _rows = story
    result = evaluate_hybrid_shadow(db, scope, messages, query="other", options=HybridOptions(recent_turns=16))
    got = [m for m in result.candidate_messages if MARKER in m]
    expected = [m for m in messages if MARKER in m]
    assert got[-16:] == expected[-16:]
    assert result.candidate_messages[-1] == messages[-1]


@pytest.mark.parametrize("change", ["gap", "unknown_reader", "historical", "changed_source", "uncertain_language"])
def test_missing_proof_or_uncertain_history_returns_exact_full_baseline(story, change):
    db, scope, messages, rows = story
    if change == "gap":
        db.execute("DELETE FROM memory_segments WHERE start_id=? AND layer='summary'", (rows[12][0],))
    elif change == "unknown_reader":
        scope = replace(scope, principals=())
    elif change == "historical":
        scope = replace(scope, historical=True)
    else:
        index = 15
        text = "Changed source." if change == "changed_source" else "秘密を守る約束です。"
        db.execute("UPDATE messages SET content=? WHERE id=?", (text, rows[index][0]))
    db.commit()
    result = evaluate_hybrid_shadow(db, scope, messages, query="astrolabe")
    assert result.dispatch_messages == result.candidate_messages == messages
    assert result.metrics["candidate_status"] == "fallback"
    assert result.metrics["omitted_turns"] == 0


def test_short_history_negative_control_is_unchanged(tmp_path):
    db, scope, messages, _ = native_fixture(tmp_path, count=8)
    try:
        result = evaluate_hybrid_shadow(db, scope, messages, query="astrolabe")
        assert result.candidate_messages == result.dispatch_messages == messages
        assert result.metrics["estimated_reduction_fraction"] == 0
    finally:
        db.close()


def test_read_only_input_connection_is_supported(tmp_path):
    import sqlite3

    path = tmp_path / "native.sqlite3"
    db, scope, messages, _ = native_fixture(tmp_path, db=sqlite3.connect(path))
    db.close()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as readonly, readonly:
        result = evaluate_hybrid_shadow(readonly, scope, messages, query="astrolabe")
        assert result.metrics["candidate_status"] == "preview"
        assert readonly.total_changes == 0


def test_latest_reader_change_prevents_preview(story):
    db, scope, messages, _ = story
    result = evaluate_hybrid_shadow(
        db, scope, messages, query="astrolabe", resolve_current_scope=lambda: replace(scope, principals=("mira",))
    )
    assert result.candidate_messages == messages
    assert result.metrics["native_source_verified"] is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"recent_turns": 0},
        {"recent_turns": True},
        {"relevant_turns": -1},
        {"neighbors": 0},
        {"chars_per_token": float("nan")},
    ],
)
def test_unsafe_options_rejected(kwargs):
    with pytest.raises(ValueError):
        HybridOptions(**kwargs)
