#!/usr/bin/env python3
"""Compare two matched synthetic runtime reports without authorizing deployment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REQUIRED_WORKLOADS = ("prompt_assembly", "prompt_compaction", "telegram_format")


def compare_reports(
    baseline: dict,
    candidate: dict,
    *,
    min_compaction_gain_pct: float = 5.0,
    max_other_regression_pct: float = 5.0,
    max_rss_regression_pct: float = 10.0,
) -> dict:
    """A narrow local CPU+RSS gate; not an end-to-end or JIT/quality test."""
    if baseline.get("schema_version") != 1 or candidate.get("schema_version") != 1:
        raise ValueError("unsupported benchmark schema")
    if baseline["python"]["major_minor"] != [3, 11] or candidate["python"]["major_minor"] != [3, 15]:
        raise ValueError("expected Python 3.11 baseline and Python 3.15 candidate")
    if baseline["source_sha256"] != candidate["source_sha256"]:
        raise ValueError("different benchmark or implementation source")
    if baseline["host_class"] != candidate["host_class"]:
        raise ValueError("reports are not from the same host class/kernel")
    for report in (baseline, candidate):
        if (
            report["provider_requests_issued"] != 0
            or report["production_database_writes"] != 0
            or report["raw_prompts_exported"] is not False
        ):
            raise ValueError("unexpected non-synthetic inputs")
    workloads = {}
    for name in REQUIRED_WORKLOADS:
        old = baseline["workloads"][name]
        new = candidate["workloads"][name]
        if old["semantic_sha256"] != new["semantic_sha256"]:
            raise ValueError(f"{name}: narrative or formatting output changed")
        original = float(old["cpu_ms_p95"])
        updated = float(new["cpu_ms_p95"])
        if original <= 0 or updated <= 0:
            raise ValueError("non-positive CPU measurement")
        workloads[name] = {
            "baseline_cpu_ms_p95": original,
            "candidate_cpu_ms_p95": updated,
            "change_pct": round(100 * (updated / original - 1), 2),
        }
    rss_base, rss_new = int(baseline["peak_process_rss_kib"]), int(candidate["peak_process_rss_kib"])
    if rss_base <= 0 or rss_new <= 0:
        raise ValueError("non-positive peak RSS")
    rss_change = round(100 * (rss_new / rss_base - 1), 2)
    compaction_gain = -workloads["prompt_compaction"]["change_pct"]
    other_pass = all(
        workloads[name]["change_pct"] <= max_other_regression_pct for name in ("prompt_assembly", "telegram_format")
    )
    return {
        "local_cpu_candidate_pass": (
            compaction_gain >= min_compaction_gain_pct and other_pass and rss_change <= max_rss_regression_pct
        ),
        "compaction_cpu_gain_pct": round(compaction_gain, 2),
        "rss_change_pct": rss_change,
        "workloads": workloads,
        "jit_candidate": candidate["jit"],
        "end_to_end_latency_measured": False,
        "narrative_quality_approved": False,
        "production_upgrade_authorized": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("python311", type=Path)
    parser.add_argument("python315", type=Path)
    args = parser.parse_args(argv)
    base = json.loads(args.python311.read_text(encoding="utf-8"))
    candidate = json.loads(args.python315.read_text(encoding="utf-8"))
    report = compare_reports(base, candidate)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["local_cpu_candidate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
