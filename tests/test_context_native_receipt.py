"""Native receipts derive evidence from accepted SQLite rows, never caller claims."""

import copy
import json
from dataclasses import replace

import pytest
from settings_test_support import make_test_settings
from test_context_history_codec import prompt
from test_story_memory_scope import db as db

from bridge import memory
from bridge.context_history_codec import CODEC_MARKER, pack_history
from bridge.context_native_receipt import capture_native_history, verify_native_candidate
from bridge.memory_scope_store import resolve_memory_scope
from bridge.provider_port import ProviderPort
from bridge.user_dialogue import format_user_dialogue_action


@pytest.fixture
def native(db, tmp_path):
    baseline = prompt()
    for i, message in enumerate(baseline[1:-1]):
        db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('c','s',?,?,?)",
            (message["role"], message["content"], 3 + i),
        )
        if message["role"] == "user":
            message["content"] = format_user_dialogue_action(message["content"])
    db.commit()
    settings = make_test_settings(home=tmp_path)
    result = None
    for _ in range(8):
        result = memory.generate_session_summary_result(
            db,
            "c",
            {"session_id": "s", "model_id": "m"},
            force=True,
            durable=True,
            max_segments=8,
            provider_port=ProviderPort(
                lambda *_a, **_k: json.dumps(
                    {
                        "blocks": [
                            {
                                "text": "Synthetic accepted checkpoint; literal source is retained independently.",
                                "visibility": "shared",
                                "known_by": [],
                            }
                        ]
                    }
                )
            ),
            app_settings=settings,
        )
        if result.complete:
            break
    assert result is not None and result.complete
    scope = resolve_memory_scope(db, "c", {"session_id": "s"}, {"name": "Mira"})
    assert scope is not None
    return baseline, scope


def test_native_receipt_verifies_all_source_evidence_without_database_writes(db, native):
    baseline, scope = native
    before = db.total_changes
    receipt = capture_native_history(db, scope, baseline)
    candidate, meta = pack_history(baseline)
    assert meta["encoded_turns"] > 0
    proof = verify_native_candidate(db, receipt, baseline, candidate, current_scope=scope)
    assert proof["native_source_verified"] is True
    assert proof["all_source_evidence_preserved"] is True
    assert proof["role_order_multiplicity_preserved"] is True
    assert proof["checked_source_rows"] == 22
    assert proof["semantic_equivalence_proven"] is False
    assert proof["production_activation_allowed"] is False
    assert db.total_changes == before
    assert "pact" not in repr(receipt).lower()
    assert "Mira" not in json.dumps(proof)


def test_changed_text_cannot_reuse_prior_receipt(db, native):
    baseline, scope = native
    receipt = capture_native_history(db, scope, baseline)
    candidate, _ = pack_history(baseline)
    db.execute("UPDATE messages SET content='The promise was explicitly revoked.' WHERE id=1")
    db.commit()
    proof = verify_native_candidate(db, receipt, baseline, candidate, current_scope=scope)
    assert not proof["native_source_verified"]
    assert not proof["all_source_evidence_preserved"]


@pytest.mark.parametrize("change", ["reader", "story", "history", "revision", "incarnation"])
def test_reader_story_revision_and_temporal_scope_changes_reject(db, native, change):
    baseline, scope = native
    receipt = capture_native_history(db, scope, baseline)
    candidate, _ = pack_history(baseline)
    mutations = {
        "reader": {"principals": ("bob",)},
        "story": {"session_id": "other"},
        "history": {"historical": True},
        "revision": {"rewrite_revision": scope.rewrite_revision + 1},
        "incarnation": {"session_created_at": 100.0},
    }
    proof = verify_native_candidate(db, receipt, baseline, candidate, current_scope=replace(scope, **mutations[change]))
    assert not proof["native_source_verified"]


@pytest.mark.parametrize("damage", ["coverage", "invalidation", "segment", "dirty", "delete"])
def test_native_source_coverage_is_required_not_accepted_from_caller(db, native, damage):
    baseline, scope = native
    receipt = capture_native_history(db, scope, baseline)
    candidate, _ = pack_history(baseline)
    commands = {
        "coverage": "UPDATE memory_layer_state SET covered_id=0 WHERE layer='summary'",
        "invalidation": "UPDATE memory_layer_state SET invalidated_from_id=1 WHERE layer='summary'",
        "segment": "DELETE FROM memory_segments WHERE layer='summary' AND start_id=3",
        "dirty": "UPDATE memory_jobs SET dirty_version=dirty_version+1 WHERE layer='summary'",
        "delete": "DELETE FROM sessions WHERE chat_id='c' AND session_id='s'",
    }
    db.execute(commands[damage])
    db.commit()
    proof = verify_native_candidate(db, receipt, baseline, candidate, current_scope=scope)
    assert not proof["native_source_verified"]


def test_tampered_dictionary_or_nonhistory_text_cannot_mint_retention(db, native):
    baseline, scope = native
    receipt = capture_native_history(db, scope, baseline)
    candidate, _ = pack_history(baseline)
    damaged = copy.deepcopy(candidate)
    table = next(m for m in damaged if m.get(CODEC_MARKER) == "dictionary")
    table["content"] = table["content"].replace("nobody may open", "everybody must open")
    assert not verify_native_candidate(db, receipt, baseline, damaged, current_scope=scope)[
        "all_source_evidence_preserved"
    ]
    damaged = copy.deepcopy(candidate)
    damaged[0]["content"] += " Changed policy."
    assert not verify_native_candidate(db, receipt, baseline, damaged, current_scope=scope)[
        "all_source_evidence_preserved"
    ]
    forged = replace(receipt, baseline_sha256="0" * 64)
    assert not verify_native_candidate(db, forged, baseline, candidate, current_scope=scope)["native_source_verified"]


def test_unauthenticated_prompt_text_cannot_be_attested(db, native):
    baseline, scope = native
    baseline[3]["content"] += " Unrecorded invented commitment."
    with pytest.raises(ValueError):
        capture_native_history(db, scope, baseline)


def test_source_timestamp_reorder_and_new_turn_revoke_old_snapshot(db, native):
    baseline, scope = native
    receipt = capture_native_history(db, scope, baseline)
    candidate, _ = pack_history(baseline)
    db.execute("UPDATE messages SET created_at=1000 WHERE id=2")
    db.commit()
    assert not verify_native_candidate(db, receipt, baseline, candidate, current_scope=scope)["native_source_verified"]


def test_source_snapshot_cannot_be_replaced_by_synthetic_authority_flags(db, native):
    baseline, scope = native
    candidate, _ = pack_history(baseline)
    forged = {"native_source_verified": True, "semantic_equivalence_proven": True}
    result = verify_native_candidate(db, forged, baseline, candidate, current_scope=scope)
    assert result["reason_codes"] == ["invalid_native_receipt"]
    assert result["production_activation_allowed"] is False
