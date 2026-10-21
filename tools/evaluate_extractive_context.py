#!/usr/bin/env python3
"""Provider-free complete synthetic prompts and explicitly partial live diagnostics."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bridge.memory_fact_store import digest_value  # noqa: E402
from bridge.settings import load_app_settings  # noqa: E402
from tools.evaluate_statement_context import CASES, CODE_FILES, _synthetic_messages  # noqa: E402
from tools.extractive_context import evaluate_extractive_context  # noqa: E402
from tools.hybrid_context_fixture import native_fixture  # noqa: E402

BOUND_FILES = tuple(
    sorted(
        set(CODE_FILES)
        | {str(path.relative_to(ROOT)) for path in (ROOT / "bridge").rglob("*.py")}
        | {
            "bridge/context_native_receipt.py",
            "bridge/context_history_codec.py",
            "tools/extractive_context.py",
            "tools/evaluate_extractive_context.py",
            "tools/extractive_live_replay.py",
        }
    )
)


def implementation_digest() -> str:
    return digest_value({name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in BOUND_FILES})


def build_report(directory: Path) -> dict:
    cases = []
    for name in CASES:
        db, scope, _, rows = native_fixture(directory, count=8 if name == "short_history" else 44, variant=name)
        try:
            messages = _synthetic_messages(db, scope, rows, load_app_settings({}, home=directory), name)
            query = "astrolab pirus" if name == "indonesian" else "turquoise astrolabe"
            result = evaluate_extractive_context(db, scope, messages, query=query)
            cases.append({"case_id": name, "weight": 1 / len(CASES), "source_rows": len(rows), **result.metrics})
        finally:
            db.close()
    baseline = sum(case["baseline_tokens"] for case in cases)
    candidate = sum(case["candidate_tokens"] for case in cases)
    reduction = 1 - candidate / baseline
    return {
        "schema_version": 1,
        "type": "extractive_v2_complete_declared_synthetic_prompts",
        "implementation_sha256": implementation_digest(),
        "provider_requests": 0,
        "production_database_writes": 0,
        "production_activation_allowed": False,
        "provider_measured_reduction": None,
        "independent_human_review_complete": False,
        "all_source_evidence_preserved": False,
        "semantic_continuity_proven": False,
        "estimated": {
            "complete_prompt_baseline_tokens": baseline,
            "complete_prompt_candidate_tokens": candidate,
            "aggregate_reduction_fraction": reduction,
            "case_weighted_reduction_fraction": sum(c["estimated_reduction_fraction"] for c in cases) / len(cases),
        },
        "screening": {
            "target_fraction": 0.30,
            "estimated_target_met": reduction >= 0.30,
            "provider_trial_executed": False,
            "matched_provider_usage_available": False,
        },
        "cases": cases,
        "limitations": [
            "Character estimates are not provider-reported tokens.",
            "Complete as declared synthetic workload, not a production traffic distribution.",
            "Baseline source authentication does not prove omitted facts redundant.",
            "All six cases and fallback denominators are retained.",
            "No independent blinded human narrative scoring or new model outputs exist.",
        ],
    }


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        os.chmod(path, 0o600)
        json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--metadata-only", action="store_true")
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        parser.error("output must be a new checkpoint")
    if bool(args.database) != bool(args.metadata_only):
        parser.error("live database requires --metadata-only")
    if args.database:
        from bridge.environment import bootstrap_environment
        from tools.extractive_live_replay import build_live_report

        env = dict(os.environ)
        bootstrap_environment(env)
        report = build_live_report(args.database, load_app_settings(env, home=Path.home()))
        report["implementation_sha256"] = implementation_digest()
    else:
        with tempfile.TemporaryDirectory(prefix="extractive-v2-") as directory:
            report = build_report(Path(directory))
    write_report(args.output, report)
    print(
        json.dumps(
            {
                "type": report["type"],
                "cases": len(report["cases"]),
                "estimated": report.get("estimated"),
                "provider_requests": 0,
                "production_activation_allowed": False,
            },
            allow_nan=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
