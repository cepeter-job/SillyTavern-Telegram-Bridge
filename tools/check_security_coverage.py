"""Fail CI when security-critical module coverage falls below its versioned floor."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

DEFAULT_POLICY = Path(__file__).with_name("security_coverage_baseline.json")


def check_security_coverage(coverage_path: Path, policy_path: Path = DEFAULT_POLICY) -> tuple[str, ...]:
    coverage = json.loads(Path(coverage_path).read_text(encoding="utf-8"))
    policy = json.loads(Path(policy_path).read_text(encoding="utf-8"))
    files = coverage.get("files", {})
    minimums = policy.get("minimum_combined_percent", {})
    failures: list[str] = []
    for module, minimum_raw in sorted(minimums.items()):
        minimum = float(minimum_raw)
        summary = files.get(module, {}).get("summary", {})
        actual_raw = summary.get("percent_covered")
        if actual_raw is None:
            line = f"{module}: missing from coverage report (minimum {minimum:.2f}%)"
            print(line)
            failures.append(line)
            continue
        actual = float(actual_raw)
        print(f"{module}: {actual:.2f}% (minimum {minimum:.2f}%)")
        if actual + 1e-9 < minimum:
            failures.append(f"{module}: {actual:.2f}% < {minimum:.2f}%")
    return tuple(failures)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check dedicated security-critical coverage floors.")
    parser.add_argument("coverage", type=Path, nargs="?", default=Path("coverage.json"))
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    args = parser.parse_args()
    failures = check_security_coverage(args.coverage, args.policy)
    if failures:
        print("security coverage regressions:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
