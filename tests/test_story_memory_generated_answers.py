"""Real CLI contracts: opt-in dispatch, conservative caps and honest scoring."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from retrieval_http_test_support import wire_server

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "tools/evaluate_story_memory_answers.py"
COMPASS = "The brass compass is beneath the northern observatory staircase."
SAFE = "The iron safe stands beside a green ivy pot in the archive."
CODE = "The iron safe's access code is orchid-27."


def run_cli(output, *args):
    env = dict(os.environ, STORY_EVAL_TEST_KEY="synthetic-test-key")
    return subprocess.run(
        [sys.executable, str(CLI), "--output", str(output), *args],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def live_args(endpoint, *extra):
    return [
        "--enable-live-model",
        "--endpoint",
        endpoint + "/v1/chat/completions",
        "--model",
        "synthetic-wire-model",
        "--key-env",
        "STORY_EVAL_TEST_KEY",
        "--queries",
        "Q01,Q14",
        "--request-budget",
        "2",
        "--input-token-budget",
        "30000",
        "--output-token-budget",
        "2000",
        "--max-output-tokens",
        "1000",
        "--context-token-cap",
        "16000",
        "--input-usd-per-million",
        "1",
        "--output-usd-per-million",
        "2",
        "--max-cost-usd",
        "1",
        *extra,
    ]


def response(content, *, usage=None, finish_reason="stop"):
    result = {
        "id": "synthetic-completion",
        "object": "chat.completion",
        "model": "synthetic-wire-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": json.dumps(content)},
                "finish_reason": finish_reason,
            }
        ],
    }
    if usage is not None:
        result["usage"] = usage
    return 200, result, {}


def test_default_real_cli_plans_without_model_evidence(tmp_path):
    output = tmp_path / "offline.json"
    process = run_cli(output, "--queries", "Q01,Q14")
    assert process.returncode == 0, process.stderr
    report = json.loads(output.read_text())
    assert report["mode"] == "offline_plan"
    assert report["network"]["requests"] == 0
    assert report["model_evaluation"]["status"] == "not_executed"
    assert report["narrative_quality"]["status"] == "human_review_required"
    assert [case["query_id"] for case in report["cases"]] == ["Q01", "Q14"]
    assert report["cases"][0]["required_fact_keys"] == ["M01"]
    assert "M13" in report["cases"][1]["forbidden_fact_keys"]
    assert report["source"]["revision"]
    assert report["fixture_sha256"]


def test_default_in_process_rejects_every_network_seam(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "tools"))
    import socket

    from evaluate_story_memory_answers import main
    from story_memory_retrieval_http import BoundedHTTP

    def unexpected(*args, **kwargs):
        pytest.fail("Default answer plan attempted network")

    monkeypatch.setattr(socket.socket, "connect", unexpected)
    monkeypatch.setattr(socket.socket, "connect_ex", unexpected)
    monkeypatch.setattr(socket, "getaddrinfo", unexpected)
    monkeypatch.setattr(BoundedHTTP, "request", unexpected)
    assert main(["--output", str(tmp_path / "offline.json"), "--queries", "Q01"]) == 0


def test_live_wire_scores_prose_leak_separately_from_valid_extraction(tmp_path):
    def respond(call):
        if len(calls) == 1:
            return response({"answer": COMPASS, "facts": [{"key": "M01", "statement": COMPASS}], "narrative": COMPASS})
        return response({"answer": SAFE, "facts": [{"key": "M16", "statement": SAFE}], "narrative": SAFE + " " + CODE})

    output = tmp_path / "live.json"
    with wire_server(respond) as (endpoint, calls):
        process = run_cli(output, *live_args(endpoint))
    assert process.returncode == 1, process.stderr
    assert len(calls) == 2
    assert all(call["path"] == "/v1/chat/completions" for call in calls)
    assert all(call["payload"]["max_tokens"] == 1000 for call in calls)
    report = json.loads(output.read_text())
    first, second = report["cases"]
    assert first["scoring"]["extraction"]["missing_required"] == []
    assert first["scoring"]["prose"]["missing_required_answer"] == []
    assert second["scoring"]["extraction"]["forbidden_claims"] == []
    assert second["scoring"]["prose"]["forbidden_mentions"] == ["M13", "A13"]
    assert report["model_evaluation"]["status"] == "completed"
    assert report["narrative_quality"]["status"] == "human_review_required"
    assert "synthetic-test-key" not in output.read_text()
    assert CODE not in calls[1]["payload"]["messages"][1]["content"]


@pytest.mark.parametrize(
    "extra",
    [
        ["--request-budget", "1"],
        ["--input-token-budget", "1"],
        ["--output-token-budget", "1"],
        ["--context-token-cap", "1"],
        ["--max-cost-usd", "0.000001"],
        ["--max-cost-usd", "nan"],
        ["--input-usd-per-million", "-1"],
        ["--output-usd-per-million", "inf"],
        ["--key-env", "STORY_EVAL_MISSING_KEY"],
        ["--queries", "Q99"],
    ],
)
def test_invalid_live_preflight_never_dispatches(tmp_path, extra):
    with wire_server(lambda call: pytest.fail("Invalid preflight dispatched")) as (endpoint, calls):
        process = run_cli(tmp_path / "invalid.json", *live_args(endpoint, *extra))
    assert process.returncode == 2
    assert not calls


def test_live_configuration_without_opt_in_is_rejected(tmp_path):
    with wire_server(lambda call: pytest.fail("Missing opt-in dispatched")) as (endpoint, calls):
        process = run_cli(tmp_path / "invalid.json", "--endpoint", endpoint)
    assert process.returncode == 2
    assert not calls


@pytest.mark.parametrize("kind", ["http", "malformed", "over_usage", "truncated", "over_text", "secret_echo"])
def test_bad_response_stops_without_retry_or_second_dispatch(tmp_path, kind):
    def respond(call):
        if kind == "http":
            return 429, {"error": {"message": "PRIVATE synthetic-test-key"}}, {}
        if kind == "malformed":
            return response({"answer": "text"})
        if kind == "over_text":
            return response({"answer": "x" * 2000, "facts": [], "narrative": ""})
        if kind == "secret_echo":
            return response({"answer": "synthetic-test-key", "facts": [], "narrative": ""})
        return response(
            {"answer": "", "facts": [], "narrative": ""},
            usage={"prompt_tokens": 1, "completion_tokens": 1001, "total_tokens": 1002}
            if kind == "over_usage"
            else None,
            finish_reason="length" if kind == "truncated" else "stop",
        )

    output = tmp_path / "failed.json"
    with wire_server(respond) as (endpoint, calls):
        process = run_cli(output, *live_args(endpoint))
    assert process.returncode == 1, process.stderr
    assert len(calls) == 1
    report = json.loads(output.read_text())
    assert report["network"]["requests"] == 1
    assert report["model_evaluation"]["status"] == "failed"
    assert "PRIVATE" not in output.read_text()
    assert "synthetic-test-key" not in output.read_text()


@pytest.mark.parametrize(
    ("escaped_text", "error"),
    [(r"\u0073ynthetic-test-key", "credential_echo"), (r"\ud800", "invalid_answer_unicode")],
)
def test_decoded_response_strings_fail_safely_before_next_dispatch(tmp_path, escaped_text, error):
    def respond(call):
        status, result, headers = response(
            {"answer": COMPASS, "facts": [{"key": "M01", "statement": COMPASS}], "narrative": COMPASS + " MARKER"}
        )
        result["choices"][0]["message"]["content"] = result["choices"][0]["message"]["content"].replace(
            "MARKER", escaped_text
        )
        return status, result, headers

    output = tmp_path / "failed.json"
    output.write_text("previous report", encoding="utf-8")
    with wire_server(respond) as (endpoint, calls):
        process = run_cli(output, *live_args(endpoint))
    assert process.returncode == 1, process.stderr
    assert len(calls) == 1
    report_text = output.read_text(encoding="utf-8")
    report = json.loads(report_text)
    assert report["cases"][0]["status"] == "failed"
    assert report["cases"][0]["error"] == error
    assert "generated" not in report["cases"][0]
    assert report["cases"][1]["status"] == "not_executed"
    assert report["network"]["requests"] == 1
    assert report["model_evaluation"]["status"] == "failed"
    assert "synthetic-test-key" not in report_text + process.stdout + process.stderr
