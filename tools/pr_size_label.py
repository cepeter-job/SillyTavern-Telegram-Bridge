"""Apply exactly one canonical ``size:*`` label to a pull request.

Run by ``.github/workflows/pr-size-labeler.yml`` on ``pull_request_target``,
which is the only trigger that can label a fork pull request. The workflow never
checks out pull-request code: it fetches this file from the trusted base
revision and passes only ``GH_TOKEN``, ``GITHUB_REPOSITORY`` and ``PR_NUMBER``.

The size class counts changed lines (additions + deletions) reported by the API,
so the label matches the diff a reviewer sees rather than local hook state.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from collections.abc import Iterable
from urllib.parse import quote

# Inclusive upper bound of changed lines for each bounded size class.
SIZE_THRESHOLDS: dict[str, int] = {
    "size/xs": 10,
    "size/s": 50,
    "size/m": 250,
    "size/l": 1000,
}
SIZE_LABELS = (*SIZE_THRESHOLDS, "size/xl")
LABEL_COLORS = {
    "size/xs": "C2E0C6",
    "size/s": "BFD4F2",
    "size/m": "FEF2C0",
    "size/l": "F9D0C4",
    "size/xl": "E11D21",
}
LABEL_DESCRIPTION = "Pull-request size class applied by the PR size labeler workflow"
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def size_label(changed_lines: int) -> str:
    """Return the canonical size label for a changed-line count."""
    if changed_lines < 0:
        raise ValueError("changed_lines must not be negative")
    for label, limit in SIZE_THRESHOLDS.items():
        if changed_lines <= limit:
            return label
    return SIZE_LABELS[-1]


def label_plan(changed_lines: int, existing_labels: Iterable[str]) -> tuple[str, list[str], bool]:
    """Return the target label, stale size labels to remove, and whether to add the target."""
    existing = set(existing_labels)
    target = size_label(changed_lines)
    stale = sorted(label for label in existing if label in SIZE_LABELS and label != target)
    return target, stale, target not in existing


def _gh(*arguments: str) -> str:
    result = subprocess.run(  # noqa: S603 -- fixed local gh invocation with validated arguments
        [shutil.which("gh") or "/usr/bin/gh", *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode:
        raise RuntimeError(f"gh {' '.join(arguments)} failed: {result.stderr.strip()}")
    return result.stdout


def _existing_labels(raw: str) -> list[str]:
    return [label.strip() for label in raw.split(",") if label.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print the plan without changing labels")
    parser.add_argument("--changed-lines", type=int, help="Classify this count instead of reading the pull request")
    parser.add_argument("--labels", default="", help="Comma-separated labels the pull request already has")
    arguments = parser.parse_args(argv)

    if arguments.dry_run and arguments.changed_lines is not None:
        target, stale, needs_target = label_plan(arguments.changed_lines, _existing_labels(arguments.labels))
        print(
            f"dry run: {arguments.changed_lines} changed lines -> {target} "
            f"(add={needs_target}, remove={stale or 'none'})"
        )
        return 0

    repository = os.environ.get("GITHUB_REPOSITORY", "")
    number = os.environ.get("PR_NUMBER", "")
    if not _REPOSITORY.match(repository):
        raise SystemExit("GITHUB_REPOSITORY must be an owner/name pair")
    if not number.isdigit():
        raise SystemExit("PR_NUMBER must be a pull-request number")

    try:
        pull = json.loads(_gh("api", f"repos/{repository}/pulls/{number}"))
    except (RuntimeError, json.JSONDecodeError, KeyError) as error:
        raise SystemExit(f"could not read pull request metadata: {error}") from error
    changed_lines = int(pull["additions"]) + int(pull["deletions"])
    target, stale, needs_target = label_plan(changed_lines, (label["name"] for label in pull.get("labels", [])))
    print(f"PR #{number}: {changed_lines} changed lines -> {target} (add={needs_target}, remove={stale or 'none'})")
    if arguments.dry_run or not (needs_target or stale):
        return 0

    try:
        if needs_target:
            _gh(
                "label",
                "create",
                target,
                "--repo",
                repository,
                "--force",
                "--color",
                LABEL_COLORS[target],
                "--description",
                LABEL_DESCRIPTION,
            )
            _gh("api", "--method", "POST", f"repos/{repository}/issues/{number}/labels", "-f", f"labels[]={target}")
        for label in stale:
            _gh("api", "--method", "DELETE", f"repos/{repository}/issues/{number}/labels/{quote(label, safe='')}")
    except RuntimeError as error:
        raise SystemExit(f"could not update labels: {error}") from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
