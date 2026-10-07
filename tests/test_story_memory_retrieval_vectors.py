"""Exact vector ranks are subordinate to the complete canonical eligible corpus."""

import json
import math
import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


@pytest.fixture
def small(tmp_path):
    from story_memory_retrieval_corpus import RetrievalRuntime

    from bridge.memory_fact_store import load_current_fact, remember_local_fact

    with RetrievalRuntime(tmp_path) as runtime:
        mid = remember_local_fact(runtime.db, "evaluation", "main", "Rowan", "The old sentinel releases the captive.")
        yield runtime, load_current_fact(runtime.db, mid)


def scope(runtime, reader="Rowan"):
    from bridge.memory_scope_store import resolve_memory_scope

    return resolve_memory_scope(runtime.db, "evaluation", runtime.session, {"name": reader})


def profile():
    from story_memory_retrieval_vectors import EmbeddingProfile

    return EmbeddingProfile("a" * 24, "contract-fixture", 2, "1", "contract_only", "synthetic")


def test_ineligible_high_similarity_cannot_starve_eligible_results(small):
    from story_memory_retrieval_vectors import BlobIndex, blob_rank

    from bridge.memory_fact_store import load_current_fact, remember_local_fact

    runtime, target = small
    index = BlobIndex(runtime.db, profile())
    index.put(target, [0.8, 0.6])
    for number in range(211):
        mid = remember_local_fact(runtime.db, "evaluation", "main", "Rowan", f"Unrelated old ledger item {number}.")
        index.put(load_current_fact(runtime.db, mid), [0, 1])
    for number in range(65):
        mid = remember_local_fact(runtime.db, "evaluation", "main", "Mira", f"Private restricted item {number}.")
        index.put(load_current_fact(runtime.db, mid), [1, 0])
    ranked = blob_rank(index, scope(runtime), [1, 0])
    assert ranked.ordered_memory_ids[0] == target.memory_id
    assert len(ranked.ordered_memory_ids) == 212
    assert ranked.details["eligible_count"] == 212
    assert ranked.details["scored_count"] == 212
    assert ranked.scores[0] == pytest.approx(0.8)


def test_blob_profile_and_content_hash_fence(small):
    from story_memory_retrieval_vectors import BlobIndex, blob_rank

    runtime, target = small
    index = BlobIndex(runtime.db, profile())
    index.put(target, [1, 0])
    wrong_profile = BlobIndex(runtime.db, replace(profile(), profile_id="b" * 24))
    assert blob_rank(wrong_profile, scope(runtime), [1, 0]).ordered_memory_ids == ()
    runtime.db.execute("UPDATE evaluation_vectors SET content_hash='stale'")
    assert blob_rank(index, scope(runtime), [1, 0]).ordered_memory_ids == ()


@pytest.mark.parametrize("vector", [[0, 0], [math.nan, 1], [math.inf, 0], [1], [1, 0, 0], [True, 1]])
def test_zero_nonfinite_wrong_dimensions_rejected(small, vector):
    from story_memory_retrieval_vectors import BlobIndex

    runtime, target = small
    with pytest.raises(ValueError):
        BlobIndex(runtime.db, profile()).put(target, vector)


def test_session_recreate_rejects_old_vector(small):
    from story_memory_retrieval_vectors import BlobIndex, blob_rank

    from bridge.session_core import create_session

    runtime, target = small
    before = scope(runtime)
    index = BlobIndex(runtime.db, profile())
    index.put(target, [1, 0])
    runtime.db.execute("DELETE FROM sessions WHERE chat_id='evaluation' AND session_id='main'")
    runtime.db.commit()
    runtime.session = create_session(
        runtime.db, "evaluation", "offline::fixture", session_id="main", app_settings=runtime.settings
    )
    assert blob_rank(index, before, [1, 0]).ordered_memory_ids == ()
    assert blob_rank(index, scope(runtime), [1, 0]).ordered_memory_ids == ()


def test_fact_delete_rejects_cached_vector(small):
    from story_memory_retrieval_vectors import BlobIndex, blob_rank

    runtime, target = small
    index = BlobIndex(runtime.db, profile())
    index.put(target, [1, 0])
    runtime.db.execute("DELETE FROM episodic_memories WHERE memory_id=?", (target.memory_id,))
    runtime.db.commit()
    assert blob_rank(index, scope(runtime), [1, 0]).ordered_memory_ids == ()


def test_finalizer_ignores_remote_text_and_rechecks_late_mutation(small):
    from story_memory_retrieval_rank import candidate_ranking, finalize_ranking

    runtime, target = small
    captured = scope(runtime)
    ranked = candidate_ranking(
        "hindsight", [target], "genuine_model_retrieval", details={"remote_text": "UNTRUSTED WRONG ANSWER"}
    )
    final = finalize_ranking(runtime.db, captured, ranked)
    assert final.summaries == ("The old sentinel releases the captive.",)
    assert "UNTRUSTED" not in json.dumps(final.summaries)
    runtime.db.execute(
        "UPDATE episodic_memories SET summary='Different content' WHERE memory_id=?", (target.memory_id,)
    )
    assert finalize_ranking(runtime.db, captured, ranked).ordered_memory_ids == ()


def test_float32_blob_decode_rejects_truncation_and_corruption(small):
    from story_memory_retrieval_vectors import BlobIndex, blob_rank

    runtime, target = small
    index = BlobIndex(runtime.db, profile())
    index.put(target, [1, 0])
    runtime.db.execute("UPDATE evaluation_vectors SET vector=?", (b"short",))
    with pytest.raises(ValueError, match="length"):
        blob_rank(index, scope(runtime), [1, 0])


def test_fts_reuses_production_order_and_reports_explicit_fallback(small):
    from story_memory_retrieval_rank import fts_rank

    from bridge.memory_search_store import search_fact_ids

    runtime, target = small
    current = scope(runtime)
    ranked = fts_rank(runtime.db, current, "sentinel")
    assert list(ranked.ordered_memory_ids) == search_fact_ids(runtime.db, current, "sentinel")
    assert ranked.details["explicit_assertion_ids"] == [target.memory_id]
    assert fts_rank(runtime.db, current, "unrelated").ordered_memory_ids == (target.memory_id,)
