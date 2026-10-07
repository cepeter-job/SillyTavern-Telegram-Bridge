"""Fake wire scores prove transport contracts, never semantic quality."""

import json
import sys
from pathlib import Path

import pytest
from retrieval_http_test_support import OwnedBankServer, wire_server

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
SOURCE = {"revision": "b" * 40, "dirty": False, "source_sha256": "d" * 64}


def test_external_relay_not_classified_as_free_local(tmp_path):
    from story_memory_retrieval_corpus import RetrievalCorpus
    from story_memory_retrieval_live import embedding_client, fetch_embeddings

    def respond(call):
        count = len(call["payload"]["input"])
        return (
            200,
            {"model": "fixture-model", "data": [{"index": i, "embedding": [0.8, 0.6]} for i in range(count)]},
            {},
        )

    with wire_server(respond) as (endpoint, calls):
        client = embedding_client(endpoint + "/v1/embeddings", 5)
        with RetrievalCorpus(tmp_path / "home") as corpus:
            result = fetch_embeddings(
                corpus, client, model="fixture-model", dimensions=2, revision="r1", source_identity=SOURCE
            )
            assert result["status"] == "available"
            artifact = result["artifact"]
            assert artifact["profile"]["provider_kind"] == "external_provider"
            assert len(artifact["inputs"]) == 66
            assert [len(call["payload"]["input"]) for call in calls] == [16, 16, 16, 16, 2]
            assert calls[0]["payload"]["input"][0] == "The brass compass is beneath the northern observatory staircase."
            assert artifact["acquisition"]["query_latency_boundary"] == "batched_acquisition_not_per_query_latency"


def test_partial_embeddings_do_not_become_quality_results(tmp_path):
    from story_memory_retrieval_corpus import RetrievalCorpus
    from story_memory_retrieval_live import embedding_client, fetch_embeddings

    with wire_server(lambda call: (200, {"data": [{"index": 0, "embedding": [1, 0]}]}, {})) as (endpoint, calls):
        client = embedding_client(endpoint, 5)
        with RetrievalCorpus(tmp_path / "home") as corpus:
            result = fetch_embeddings(
                corpus, client, model="fixture-model", dimensions=2, revision="r1", source_identity=SOURCE
            )
            assert result["status"] == "failed" and result["artifact"] is None
            assert result["requested_input_count"] == 66
            assert result["failed_input_count"] == 16 and result["not_attempted_input_count"] == 50
            assert len(calls) == 1


def test_hindsight_uses_owned_prefix_and_no_existing_bank(tmp_path):
    from story_memory_retrieval_corpus import RetrievalCorpus
    from story_memory_retrieval_hindsight import hindsight_client, run_hindsight_study

    server = OwnedBankServer()
    with wire_server(server) as (endpoint, calls):
        client = hindsight_client(endpoint, 33)
        with RetrievalCorpus(tmp_path / "home") as corpus:
            result = run_hindsight_study(corpus, client, tmp_path / "owned.json", source_identity=SOURCE)
            assert result["status"] == "available"
            manifest = json.loads((tmp_path / "owned.json").read_text())
            assert manifest["bank_id"].startswith("story-memory-eval-")
            assert manifest["fresh_absence_observed"] and manifest["observations_disabled_verified"]
            assert not manifest["cleanup_pending"] and manifest["cleanup_absence_observed"]
            assert calls[0]["method"] == "GET" and calls[0]["path"].endswith("/config")
            assert calls[1]["payload"] == {"name": "story-memory-eval", "enable_observations": False}
            retains = [call for call in calls if call["path"].endswith("/memories")]
            assert [len(call["payload"]["items"]) for call in retains] == [12, 12, 12, 6]
            assert len([call for call in calls if call["path"].endswith("/recall")]) == 24
            assert len(result["retained_documents"]) == 42
            assert (
                corpus.db.execute("SELECT COUNT(*) FROM memory_fact_index WHERE state='retained'").fetchone()[0] == 42
            )
            for call in calls:
                assert manifest["bank_id"] in call["path"]
                if call["path"].endswith("/recall"):
                    assert call["payload"]["types"] == ["world", "experience"]
                    assert call["payload"]["tags_match"] == "all_strict"
                    assert call["payload"]["budget"] == "low" and call["payload"]["max_tokens"] == 1024
                    assert call["payload"]["prefer_observations"] is False
            assert "PRIVATE" not in json.dumps(manifest)
            assert not server.exists


@pytest.mark.parametrize("failure", ["create", "retain"])
def test_cleanup_only_deletes_owned_bank_and_preserves_ambiguous_mutation(tmp_path, failure):
    from story_memory_retrieval_corpus import RetrievalCorpus
    from story_memory_retrieval_hindsight import hindsight_client, run_hindsight_study

    server = OwnedBankServer(fail_create=failure == "create", fail_retain=failure == "retain")
    with wire_server(server) as (endpoint, calls):
        client = hindsight_client(endpoint, 33)
        with RetrievalCorpus(tmp_path / "home") as corpus:
            result = run_hindsight_study(corpus, client, tmp_path / "owned.json", source_identity=SOURCE)
            manifest = json.loads((tmp_path / "owned.json").read_text())
            assert result["status"] == "failed"
            assert manifest["cleanup_pending"] and manifest["cleanup_absence_observed"]
            assert manifest["uncertain_creation" if failure == "create" else "uncertain_ingestion"]
            assert len([call for call in calls if call["method"] == "DELETE"]) == 1
            assert not any(call["path"].endswith("/recall") for call in calls)


def test_existing_bank_is_never_adopted_or_deleted(tmp_path):
    from story_memory_retrieval_corpus import RetrievalCorpus
    from story_memory_retrieval_hindsight import hindsight_client, run_hindsight_study

    with wire_server(OwnedBankServer(existing=True)) as (endpoint, calls):
        client = hindsight_client(endpoint, 33)
        with RetrievalCorpus(tmp_path / "home") as corpus:
            result = run_hindsight_study(corpus, client, tmp_path / "owned.json", source_identity=SOURCE)
            assert result["status"] == "failed"
            assert [call["method"] for call in calls] == ["GET"]


def test_unverified_observation_setting_prevents_ingestion(tmp_path):
    from story_memory_retrieval_corpus import RetrievalCorpus
    from story_memory_retrieval_hindsight import hindsight_client, run_hindsight_study

    with wire_server(OwnedBankServer(config_disabled=False)) as (endpoint, calls):
        client = hindsight_client(endpoint, 33)
        with RetrievalCorpus(tmp_path / "home") as corpus:
            result = run_hindsight_study(corpus, client, tmp_path / "owned.json", source_identity=SOURCE)
            assert result["status"] == "failed"
            assert not any(call["method"] == "POST" for call in calls)


def test_remote_failure_preserves_error_denominator_and_no_credentials(tmp_path):
    from story_memory_retrieval_corpus import RetrievalCorpus
    from story_memory_retrieval_hindsight import hindsight_client, run_hindsight_study
    from story_memory_retrieval_report import _observe_hindsight

    server = OwnedBankServer()
    seen = 0

    def respond(call):
        nonlocal seen
        if call["path"].endswith("/recall"):
            seen += 1
            if seen == 3:
                return 500, {"error": "PRIVATE CREDENTIAL PROVIDER BODY"}, {}
        return server(call)

    with wire_server(respond) as (endpoint, _):
        client = hindsight_client(endpoint, 33, token="PRIVATE CREDENTIAL")
        with RetrievalCorpus(tmp_path / "home") as corpus:
            result = run_hindsight_study(corpus, client, tmp_path / "owned.json", source_identity=SOURCE)
            assert len(result["rankings"]) == 24 and result["status"] == "failed"
            assert result["rankings"]["Q03"].status == "failed"
            assert sum(rank.status == "available" for rank in result["rankings"].values()) == 23
            assert "PRIVATE" not in json.dumps(result["manifest"])
            assert "PRIVATE" not in json.dumps(client.observations)
            assert not result["manifest"]["cleanup_pending"]
            _, fused = _observe_hindsight(corpus, result)
            assert fused["queries"][2]["semantic_failure_fts_fallback"] is True
            assert fused["queries"][2]["status"] == "failed"
            assert fused["queries"][2]["channel_membership"]["recall"] == []


def test_recall_does_not_admit_result_65_after_invalid_raw_candidates(tmp_path):
    from story_memory_retrieval_corpus import RetrievalCorpus
    from story_memory_retrieval_hindsight import hindsight_client, run_hindsight_study

    server = OwnedBankServer()

    def respond(call):
        status, data, options = server(call)
        if call["path"].endswith("/recall"):
            valid = data["results"][0]
            data["results"] = [dict(valid, id=f"invalid-{i}", type="observation") for i in range(64)]
            data["results"].append(dict(valid, id="eligible-result-65"))
        return status, data, options

    with wire_server(respond) as (endpoint, _):
        client = hindsight_client(endpoint, 33)
        with RetrievalCorpus(tmp_path / "home") as corpus:
            result = run_hindsight_study(corpus, client, tmp_path / "owned.json", source_identity=SOURCE)
            rank = result["rankings"]["Q01"]
            assert rank.status == "available" and rank.ordered_memory_ids == ()
            assert len(rank.raw_candidate_ids) == 65
            assert rank.details["raw_record_count"] == 65
            assert rank.details["production_raw_record_limit"] == 64
            assert rank.details["truncated_record_count"] == 1
            assert rank.details["admission_record_count"] == 64


def test_malformed_returned_tags_fail_explicitly_and_preserve_attempt_timing(tmp_path):
    from story_memory_retrieval_corpus import RetrievalCorpus
    from story_memory_retrieval_hindsight import hindsight_client, run_hindsight_study

    server = OwnedBankServer()

    def respond(call):
        status, data, options = server(call)
        if call["path"].endswith("/recall"):
            data["results"][0]["tags"] = [1]
        return status, data, options

    with wire_server(respond) as (endpoint, _):
        client = hindsight_client(endpoint, 33)
        with RetrievalCorpus(tmp_path / "home") as corpus:
            result = run_hindsight_study(corpus, client, tmp_path / "owned.json", source_identity=SOURCE)
            rank = result["rankings"]["Q01"]
            assert rank.status == "failed" and rank.reason == "invalid_recall"
            assert rank.details["attempted"] is True
            assert rank.timing_ns["hindsight_recall"] > 0
