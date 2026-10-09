"""Guardrails for the offline Python runtime benchmark and comparison."""

from __future__ import annotations

import copy

import pytest

from tools.benchmark_python_runtime import _percentile, measure_case, run_benchmark
from tools.compare_python_runtime import compare_reports


def test_percentile_nearest_rank() -> None:
    assert _percentile([4, 1, 2, 3], 0.95) == 4
    assert _percentile([4, 1, 2, 3], 0.50) == 2


def test_case_rejects_nondeterministic_results() -> None:
    state = iter(["first", "second", "third", "fourth", "fifth"])
    with pytest.raises(ValueError, match="non-deterministic"):
        measure_case(lambda: next(state), iterations=5, warmup=0)


def test_case_reports_cpu_and_wall_without_payloads() -> None:
    result = measure_case(lambda: "synthetic", iterations=5, warmup=1)
    assert result["samples"] == 5
    assert len(result["semantic_sha256"]) == 64
    assert result["wall_ms_p95"] >= 0
    assert result["cpu_ms_p95"] >= 0
    assert "synthetic" not in str(result)


def test_real_synthetic_bridge_paths_smoke() -> None:
    """Exercise real builder/compaction/formatter paths with no provider or user data."""
    report = run_benchmark(iterations=5, warmup=1)
    assert report["provider_requests_issued"] == 0
    assert report["production_database_writes"] == 0
    assert report["raw_prompts_exported"] is False
    assert report["production_upgrade_authorized"] is False
    assert set(report["workloads"]) == {"prompt_assembly", "prompt_compaction", "telegram_format"}
    assert all(result["samples"] == 5 for result in report["workloads"].values())


def _fake_report(version: list[int]) -> dict:
    workloads = {
        name: {"semantic_sha256": "same", "cpu_ms_p95": 10.0}
        for name in ("prompt_assembly", "prompt_compaction", "telegram_format")
    }
    return {
        "schema_version": 1,
        "python": {"major_minor": version},
        "host_class": {"system": "Linux", "machine": "x86_64", "kernel_release": "test"},
        "source_sha256": "same-source",
        "workloads": workloads,
        "peak_process_rss_kib": 100000,
        "jit": {"available": False, "enabled": False},
        "provider_requests_issued": 0,
        "production_database_writes": 0,
        "raw_prompts_exported": False,
    }


def test_comparison_cannot_authorize_production() -> None:
    baseline = _fake_report([3, 11])
    candidate = _fake_report([3, 15])
    candidate["workloads"]["prompt_compaction"]["cpu_ms_p95"] = 9.0
    result = compare_reports(baseline, candidate)
    assert result["local_cpu_candidate_pass"] is True
    assert result["production_upgrade_authorized"] is False
    assert result["end_to_end_latency_measured"] is False


def test_comparison_rejects_semantic_drift_and_version_mismatch() -> None:
    baseline = _fake_report([3, 11])
    candidate = _fake_report([3, 15])
    candidate["workloads"]["telegram_format"]["semantic_sha256"] = "changed"
    with pytest.raises(ValueError, match="output changed"):
        compare_reports(baseline, candidate)
    other = copy.deepcopy(candidate)
    other["workloads"]["telegram_format"]["semantic_sha256"] = "same"
    other["python"]["major_minor"] = [3, 14]
    with pytest.raises(ValueError, match="expected Python"):
        compare_reports(baseline, other)
