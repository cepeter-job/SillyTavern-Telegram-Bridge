"""Exercise the observer against preserved synthetic provider payloads, not a live model."""

import json
from pathlib import Path

from bridge.prompt_prefix_profile import PrefixProfiler
from tools.postrelease_plan import digest


def archived_profile():
    root = Path(__file__).parents[1] / "docs/evidence/post-v0319-validation"
    with (root / "provider-requests.jsonl").open() as source:
        requests = {r["request_id"]: r for r in map(json.loads, source)}
    observations = json.loads((root / "provider-observations.json").read_text())["records"]
    profiler = PrefixProfiler()
    for observation in observations:
        if observation["trial"] != "hybrid_history" or observation["kind"] != "story":
            continue
        request = requests[observation["id"]]
        assert digest(request["body"]) == request["body_sha256"] == observation["body_sha256"]
        assert observation["status"] == "completed"
        profiler.observe(
            request["body"],
            {
                "session": observation["case"],
                "character": "frozen-hybrid-card",
                "reader": "frozen-synthetic-reader",
                "branch": "frozen-source-branch",
                "provider": "frozen-nanogpt-subscription-route",
                "model": request["body"]["model"],
                "stage": "provider",
            },
            usage=observation["response"]["usage"],
        )
    return profiler.report()


def test_preserved_synthetic_payloads_reconcile_usage_without_inventing_provider_savings():
    report = archived_profile()
    assert len(report["cohorts"]) == 6
    assert sum(g["observations_seen"] for g in report["cohorts"]) == 12
    assert sum(g["provider_usage"]["input_tokens_sum"] for g in report["cohorts"]) == 43249
    assert sum(g["provider_usage"]["unknown_input_samples"] for g in report["cohorts"]) == 0
    assert sum(len(g["latest_instruction_duplicates"]) for g in report["cohorts"]) == 0
    assert report["provider_savings_fraction"] is None
    assert report["cache_hit_rate_prediction"] is None
    assert report["prompt_mutations"] == 0


def test_recorded_profile_evidence_is_bound_to_current_source_and_denies_optimization():
    import hashlib

    root = Path(__file__).parents[1]
    evidence = root / "docs/evidence/issue421-prefix-profiler/RESULTS.json"
    assert evidence.is_file(), "profiler evidence not recorded"
    report = json.loads(evidence.read_text())
    for name, expected in report["source_sha256"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected
    assert report["archived_synthetic"]["observations"] == 12
    assert report["archived_synthetic"]["input_tokens_reconciled"] == 43249
    assert report["archived_synthetic"]["instruction_duplicate_groups"] == 0
    assert report["provider_requests_issued"] == 0
    assert report["optimization_authorized"] is False
