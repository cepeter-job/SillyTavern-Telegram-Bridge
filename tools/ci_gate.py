"""Fail the existing required test check unless every independent CI job succeeded."""

from __future__ import annotations

import json
import os
from pathlib import Path

REQUIRED_JOBS = ("python-tests", "miniapp-smoke", "secret-scan", "dependency-audit", "static-analysis")


def check_results(needs: object) -> tuple[bool, str]:
    """Only a complete report with successful results can satisfy branch protection."""
    if not isinstance(needs, dict):
        needs = {}
    results = {}
    for name in REQUIRED_JOBS:
        detail = needs.get(name)
        result = detail.get("result", "missing") if isinstance(detail, dict) else "missing"
        if not isinstance(result, str) or result not in {"success", "failure", "cancelled", "skipped", "missing"}:
            result = "invalid"
        results[name] = result
    summary = ["## CI results", "", "| Check | Result |", "| --- | --- |"]
    summary.extend(f"| {name} | {result} |" for name, result in results.items())
    if set(needs) - set(REQUIRED_JOBS):
        summary.extend(("", "Unexpected jobs in the dependency report; review the required check contract."))
    passed = set(needs) == set(REQUIRED_JOBS) and all(result == "success" for result in results.values())
    return passed, "\n".join(summary) + "\n"


def main() -> int:
    try:
        needs = json.loads(os.environ.get("CI_NEEDS", ""))
    except json.JSONDecodeError:
        needs = None
    passed, summary = check_results(needs)
    print(summary, end="")
    if summary_path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary_path).open("a", encoding="utf-8") as stream:
            stream.write(summary)
    if not passed:
        print("::error::CI requires complete success from every declared check.")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
