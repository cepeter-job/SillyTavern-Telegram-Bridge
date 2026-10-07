"""Default CLI stays offline; optional studies require concrete explicit caps."""

import json
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


def test_default_cli_has_zero_external_io(tmp_path, monkeypatch):
    from compare_story_memory_retrieval import main
    from story_memory_retrieval_http import BoundedHTTP

    def unexpected(*args, **kwargs):
        pytest.fail("Default comparison attempted external I/O")

    monkeypatch.setattr(socket.socket, "connect", unexpected)
    monkeypatch.setattr(socket.socket, "connect_ex", unexpected)
    monkeypatch.setattr(socket, "getaddrinfo", unexpected)
    monkeypatch.setattr(BoundedHTTP, "request", unexpected)
    output = tmp_path / "result.json"
    assert main(["--output", str(output), "--repeats", "1"]) == 0
    report = json.loads(output.read_text())
    assert report["schema_version"] == 1
    assert report["corpus"]["fact_count"] == 42 and report["corpus"]["query_count"] == 24
    assert report["corpus"]["distinct_summaries"] == 26
    assert report["corpus"]["distinct_query_texts"] == 20
    assert report["corpus"]["positive_relevance_annotations"] == 27
    assert report["corpus"]["macro_recall_at_1_ceiling"] == pytest.approx(0.8863636364)
    assert report["backends"]["fts"]["status"] == "available"
    assert report["backends"]["blob"]["status"] == "skipped"
    assert report["backends"]["hindsight"]["status"] == "skipped"
    assert "quality" not in report["backends"]["blob_contract"]
    assert "quality" not in report["selected_sets"]["fts_plus_blob_contract"]
    assert "mrr" not in report["selected_sets"]["fts_only"]["quality"]
    assert report["invariants"]["passed"]
    assert report["network"]["requests"] == 0
    assert report["timing"]["offline_warmups_per_query"] == 1
    assert len(report["corpus"]["fact_bindings"]) == 42


@pytest.mark.parametrize(
    "arguments",
    [
        ["--embedding-endpoint", "http://127.0.0.1:8891/v1/embeddings"],
        ["--enable-live-embeddings"],
        ["--enable-live-hindsight", "--hindsight-endpoint", "http://127.0.0.1:8890"],
        [
            "--enable-live-hindsight",
            "--hindsight-endpoint",
            "http://127.0.0.1:8890",
            "--hindsight-request-budget",
            "100",
            "--owned-bank-manifest",
            "owned.json",
        ],
        ["--backends", "fts", "--require-backends", "blob"],
    ],
)
def test_live_requires_explicit_flags_and_caps(arguments, tmp_path, monkeypatch):
    from compare_story_memory_retrieval import main
    from story_memory_retrieval_http import BoundedHTTP

    monkeypatch.setattr(BoundedHTTP, "request", lambda *args, **kwargs: pytest.fail("Invalid CLI dispatched HTTP"))
    with pytest.raises(SystemExit) as error:
        main(["--output", str(tmp_path / "result.json"), *arguments])
    assert error.value.code == 2


def test_require_backends_makes_a_skipped_component_nonzero(tmp_path):
    from compare_story_memory_retrieval import main

    output = tmp_path / "result.json"
    assert main(["--output", str(output), "--repeats", "1", "--require-backends", "blob"]) == 1
    report = json.loads(output.read_text())
    assert report["required_backend_failures"] == ["blob"]
    assert report["backends"]["blob"]["reason"] == "capability_or_budget_unverified"


def test_fake_transport_artifact_replays_offline_and_hindsight_fuses_verified_receipts(tmp_path, monkeypatch):
    import compare_story_memory_retrieval as cli
    from retrieval_http_test_support import OwnedBankServer, wire_server
    from story_memory_retrieval_http import BoundedHTTP

    # A fake wire contract test; this fixture never supplies empirical quality evidence.
    identity = {"revision": "b" * 40, "tree": "c" * 40, "dirty": False, "source_sha256": "d" * 64}
    monkeypatch.setattr(cli, "source_identity", lambda: identity)

    def embedding_response(call):
        return (
            200,
            {"data": [{"index": index, "embedding": [1, 0]} for index, _ in enumerate(call["payload"]["input"])]},
            {},
        )

    report_path, artifact_path, manifest_path = [tmp_path / name for name in ("live.json", "cache.json", "owned.json")]
    with wire_server(embedding_response) as (embedding_endpoint, embedding_calls):
        with wire_server(OwnedBankServer()) as (hindsight_endpoint, hindsight_calls):
            assert (
                cli.main(
                    [
                        "--output",
                        str(report_path),
                        "--repeats",
                        "1",
                        "--require-backends",
                        "fts,blob,hindsight",
                        "--enable-live-embeddings",
                        "--embedding-endpoint",
                        embedding_endpoint,
                        "--embedding-model",
                        "fake-wire-contract",
                        "--embedding-dimensions",
                        "2",
                        "--embedding-revision",
                        "test-only",
                        "--embedding-request-budget",
                        "5",
                        "--save-embedding-artifact",
                        str(artifact_path),
                        "--enable-live-hindsight",
                        "--hindsight-endpoint",
                        hindsight_endpoint,
                        "--hindsight-request-budget",
                        "33",
                        "--owned-bank-manifest",
                        str(manifest_path),
                    ]
                )
                == 0
            )
            assert len(embedding_calls) == 5 and len(hindsight_calls) == 33
    report = json.loads(report_path.read_text())
    assert report["backends"]["blob"]["denominators"]["successful_positive_queries"] == 22
    assert report["selected_sets"]["fts_plus_hindsight"]["denominators"]["successful_queries"] == 24
    assert any(row["channel_membership"]["recall"] for row in report["selected_sets"]["fts_plus_hindsight"]["queries"])
    assert "UNTRUSTED" not in report_path.read_text() and "PRIVATE" not in report_path.read_text()
    assert not json.loads(manifest_path.read_text())["cleanup_pending"]
    assert report["network"]["requests"] == 38
    assert len(report["paired_component_differences"]) == 3

    monkeypatch.setattr(BoundedHTTP, "request", lambda *args, **kwargs: pytest.fail("Cache replay attempted HTTP"))
    replay_path = tmp_path / "replay.json"
    assert (
        cli.main(
            [
                "--output",
                str(replay_path),
                "--repeats",
                "1",
                "--embedding-artifact",
                str(artifact_path),
                "--require-backends",
                "blob",
            ]
        )
        == 0
    )
    replay = json.loads(replay_path.read_text())
    assert replay["network"]["requests"] == 0
    assert replay["backends"]["blob"]["quality"] == report["backends"]["blob"]["quality"]
    assert (
        replay["corpus"]["fact_bindings"]["M01"]["authority"]["session_created_at"]
        != (report["corpus"]["fact_bindings"]["M01"]["authority"]["session_created_at"])
    )
