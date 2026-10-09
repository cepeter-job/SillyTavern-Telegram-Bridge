"""Offline verification of Python runtime benchmark inputs and output."""

import json

import pytest

from tools.benchmark_python_runtime import benchmark, main, synthetic_cases


def test_synthetic_fixtures_preserve_provenance():
    cases, close = synthetic_cases()
    try:
        assert cases["provenance_memory_dedup"]() == (40, 40, "selected")
        assert cases["context_section_estimation"]()
        assert cases["json_request_roundtrip"]()[1] == 60
        assert cases["sqlite_context_lookup"]() == (81, 75, 155)
    finally:
        close()


def test_report_contains_only_local_performance_metrics():
    report = benchmark(iterations=2, samples=3, warmup=0)
    assert report["synthetic"] is True
    assert report["provider_requests"] == 0
    assert report["schema_version"] == 1
    assert set(report["cases"]) == {
        "context_section_estimation",
        "provenance_memory_dedup",
        "json_request_roundtrip",
        "sqlite_context_lookup",
    }
    for case in report["cases"].values():
        assert case["wall_ns_per_call_median"] >= 0
        assert case["wall_ns_per_call_p95_batch"] >= case["wall_ns_per_call_median"]
        assert case["cpu_ns_per_call_median"] >= 0


@pytest.mark.parametrize("options", [{"iterations": 0}, {"samples": 2}, {"warmup": 21}])
def test_unsafe_benchmark_sizes_rejected(options):
    with pytest.raises(ValueError):
        benchmark(**options)


def test_cli_outputs_machine_readable_json(capsys):
    assert main(["--iterations", "1", "--samples", "3", "--warmup", "0"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["workload_version"] == "bridge-synthetic-v1"
    assert output["provider_requests"] == 0
