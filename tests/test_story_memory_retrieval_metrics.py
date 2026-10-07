"""Quality denominators and production selected sets are separate from contracts."""

import sys
from pathlib import Path

from test_story_memory_retrieval_comparison import corpus as corpus

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


def test_macro_recall_is_not_hit_rate_and_failed_queries_are_explicit():
    from story_memory_retrieval_metrics import aggregate_backend

    rows = [
        {
            "query_id": "one",
            "status": "available",
            "relevant_fact_keys": ["a", "b"],
            "admitted_fact_keys": ["a", "z", "b"],
            "forbidden_admitted_count": 0,
        },
        {
            "query_id": "two",
            "status": "available",
            "relevant_fact_keys": ["c"],
            "admitted_fact_keys": ["z", "c"],
            "forbidden_admitted_count": 0,
        },
        {
            "query_id": "failed",
            "status": "failed",
            "relevant_fact_keys": ["d"],
            "admitted_fact_keys": [],
            "forbidden_admitted_count": 0,
        },
        {
            "query_id": "none",
            "status": "available",
            "relevant_fact_keys": [],
            "admitted_fact_keys": ["z"],
            "forbidden_admitted_count": 0,
        },
    ]
    report = aggregate_backend(rows, "curated_synthetic_retrieval")
    assert report["status"] == "failed"
    assert report["denominators"]["expected_positive_queries"] == 3
    assert report["denominators"]["successful_positive_queries"] == 2
    assert report["quality"]["recall_at"]["1"] == 0.25
    assert report["quality"]["recall_at"]["3"] == 1
    assert report["quality"]["mrr"] == 0.75
    assert report["quality"]["no_answer_candidate_return_rate"] == 1
    assert report["failed_query_ids"] == ["failed"]
    contract = aggregate_backend(rows, "contract_only")
    assert "quality" not in contract


def test_pairs_use_only_jointly_successful_positive_query_ids():
    from story_memory_retrieval_metrics import paired_differences

    def row(key, status, ids):
        return {"query_id": key, "status": status, "relevant_fact_keys": ["a"], "admitted_fact_keys": ids}

    left = {
        "evidence_kind": "curated_synthetic_retrieval",
        "queries": [row("q1", "available", ["a"]), row("q2", "failed", [])],
    }
    right = {
        "evidence_kind": "genuine_model_retrieval",
        "queries": [row("q1", "available", ["z", "a"]), row("q2", "available", ["a"])],
    }
    pairs = paired_differences({"fts": left, "blob": right, "contract": {**right, "evidence_kind": "contract_only"}})
    assert len(pairs) == 1
    assert pairs[0]["joint_positive_query_ids"] == ["q1"]
    assert pairs[0]["right_minus_left"]["recall_at"]["1"] == -1


def test_production_fusion_reports_selected_sets_and_rechecks_evidence(corpus):
    from story_memory_retrieval_metrics import observe_fusion
    from story_memory_retrieval_rank import candidate_ranking, finalize_ranking

    from bridge.memory_scope_store import eligible_fact

    case = corpus.cases[0]
    scope = corpus.scopes[case.query_id]
    selected = [corpus.facts["M07"], corpus.facts["M01"]]
    rank = candidate_ranking("blob", selected, "contract_only")
    final = finalize_ranking(corpus.db, scope, rank)
    result = observe_fusion(corpus, case, final, channel="local_embedding")
    assert result["selection_kind"] == "production_selected_set"
    assert result["production_block_order"] == ["local_embedding", "episodic", "summary", "scene"]
    assert "mrr" not in result and "rank" not in result
    assert result["forbidden_admitted_count"] == 0
    assert result["selected_count"] <= 6
    # The production lexical path also returns the eligible cracked-compass distractor.
    assert set(result["admitted_fact_keys"]) == {"M01", "M07", "M19"}
    assert all(eligible_fact(corpus.db, scope, evidence["memory_id"]) for evidence in result["evidence"])
    assert all(not evidence["document_id"] for evidence in result["evidence"])


def test_timing_statistics_define_small_sample_percentile():
    from story_memory_retrieval_metrics import latency_summary

    summary = latency_summary([3_000_000, 1_000_000, 2_000_000])
    assert summary == {"samples": 3, "min_ms": 1, "median_ms": 2, "p95_ms": 3, "max_ms": 3}
    assert latency_summary([]) is None
