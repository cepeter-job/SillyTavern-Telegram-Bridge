"""Frozen-review regressions for ambient hooks, durable ownership and category results."""

import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from retrieval_http_test_support import OwnedBankServer, wire_server

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


def test_closure_handles_production_reconciliation_before_accept_returns(tmp_path, monkeypatch):
    from story_memory_retrieval_corpus import RetrievalCorpus

    from bridge import director_runtime, extension_registry, narrative_reconciliation
    from bridge.ending_service import load_ending_state
    from bridge.narrative_repository import load_narrative_clock

    # Install the real hook explicitly. Unrelated background admission is isolated;
    # its synchronous finale reconciliation and all acceptance/evidence remain real.
    monkeypatch.setattr(extension_registry, "_POST_RETAIN_HOOKS", {"narrative": director_runtime._post_retain})
    monkeypatch.setattr(director_runtime, "submit_background", lambda *args, **kwargs: False)
    monkeypatch.setattr(narrative_reconciliation, "submit_background", lambda *args, **kwargs: False)
    original = RetrievalCorpus.record_row
    observed = []

    def record(corpus, key, session_key, row_id, role, text):
        original(corpus, key, session_key, row_id, role, text)
        if key == "closure.resolution.assistant":
            ending = load_ending_state(corpus.db, "evaluation", "main")
            clock = load_narrative_clock(corpus.db, "evaluation", "main")
            observed.append((row_id, ending.resolution_rowid, clock["updated_through_rowid"]))

    monkeypatch.setattr(RetrievalCorpus, "record_row", record)
    with RetrievalCorpus(tmp_path) as corpus:
        actual = corpus.events["closure.resolution.assistant"]["row_id"]
        assert observed == [(actual, actual, actual)]
        assert corpus.ending_result.completed and corpus.ending_result.delivered
        assert len(corpus.facts) == 42 and corpus.branch.memory_status == "ready"


def test_owned_mutations_follow_parent_directory_fsync(tmp_path, monkeypatch):
    from story_memory_retrieval_hindsight import hindsight_client
    from story_memory_retrieval_owned_bank import OwnedBank

    syncs, mutation_phases = [], []
    original_fsync = os.fsync

    def fsync(descriptor):
        syncs.append("directory" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file")
        original_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fsync)
    with wire_server(OwnedBankServer()) as (endpoint, _):
        client = hindsight_client(endpoint, 33)
        original_request = client.request

        def request(*args, **kwargs):
            if kwargs["phase"] in {"create", "retain"}:
                assert syncs[-2:] == ["file", "directory"]
                mutation_phases.append(kwargs["phase"])
                syncs.clear()
            return original_request(*args, **kwargs)

        monkeypatch.setattr(client, "request", request)
        bank = OwnedBank(client, "a" * 64, tmp_path / "owned.json", {"revision": "b" * 40, "dirty": False})
        items = [
            {
                "document_id": f"session:synthetic:fact:{i}",
                "content": "Synthetic fact.",
                "tags": [bank.synthetic_tag, "session:synthetic", "native-fact"],
            }
            for i in range(42)
        ]
        bank.bind_documents(items)
        try:
            bank.open()
            bank.retain(items[:12])
        finally:
            bank.cleanup()
    assert mutation_phases == ["create", "retain"]
    assert not bank.manifest["cleanup_pending"]


def test_directory_fsync_failure_prevents_any_owned_remote_dispatch(tmp_path, monkeypatch):
    from story_memory_retrieval_hindsight import hindsight_client
    from story_memory_retrieval_owned_bank import OwnedBank

    client = hindsight_client("http://127.0.0.1:1", 33)
    monkeypatch.setattr(client, "request", lambda *args, **kwargs: pytest.fail("Unsynced ownership dispatched HTTP"))
    original_fsync = os.fsync

    def fsync(descriptor):
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("synthetic directory sync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fsync)
    with pytest.raises(OSError, match="synthetic directory sync failure"):
        OwnedBank(client, "a" * 64, tmp_path / "owned.json", {"revision": "b" * 40, "dirty": False})
    assert client.count == 0


def test_observations_preserve_the_predeclared_category():
    from story_memory_retrieval_metrics import query_base

    corpus = SimpleNamespace(eligible=lambda case: {})
    case = SimpleNamespace(query_id="q1", category="knowledge", relevant_fact_keys=(), forbidden_fact_keys=())
    assert query_base(corpus, case)["category"] == "knowledge"


def test_category_metrics_keep_failures_no_answers_and_contract_boundary():
    from story_memory_retrieval_metrics import aggregate_backend

    rows = [
        {
            "query_id": "k1",
            "category": "knowledge",
            "status": "available",
            "relevant_fact_keys": ["a", "b"],
            "admitted_fact_keys": ["a"],
            "forbidden_admitted_count": 0,
        },
        {
            "query_id": "k2",
            "category": "knowledge",
            "status": "failed",
            "relevant_fact_keys": ["c"],
            "admitted_fact_keys": [],
            "forbidden_admitted_count": 0,
        },
        {
            "query_id": "n1",
            "category": "no_answer",
            "status": "available",
            "relevant_fact_keys": [],
            "admitted_fact_keys": ["z"],
            "forbidden_admitted_count": 0,
        },
        {
            "query_id": "s1",
            "category": "branch",
            "status": "skipped",
            "relevant_fact_keys": ["d"],
            "admitted_fact_keys": [],
            "forbidden_admitted_count": 0,
        },
    ]
    report = aggregate_backend(rows, "genuine_model_retrieval")
    knowledge = report["categories"]["knowledge"]
    assert knowledge["denominators"]["expected_queries"] == 2
    assert knowledge["denominators"]["successful_queries"] == 1
    assert knowledge["denominators"]["failed_queries"] == 1
    assert knowledge["denominators"]["failed_positive_queries"] == 1
    assert knowledge["failed_query_ids"] == ["k2"]
    assert knowledge["quality"]["recall_at"]["1"] == 0.5
    assert knowledge["quality"]["mrr"] == 1
    assert report["categories"]["no_answer"]["quality"]["no_answer_candidate_return_rate"] == 1
    assert report["categories"]["branch"]["denominators"]["skipped_queries"] == 1
    selected = aggregate_backend(rows, "genuine_model_retrieval", selected_set=True)
    assert set(selected["categories"]["knowledge"]["quality"]["recall_at"]) == {"6"}
    assert "mrr" not in selected["categories"]["knowledge"]["quality"]
    for selected_set in (False, True):
        contract = aggregate_backend(rows, "contract_only", selected_set=selected_set)
        assert "quality" not in contract
        assert all("quality" not in group for group in contract["categories"].values())
