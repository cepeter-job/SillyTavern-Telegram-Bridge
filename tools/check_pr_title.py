"""Check a pull-request title against the repository commit convention.

Pull-request titles become squash-commit subjects on ``main``. A shared
``type(scope): subject`` prefix keeps history scannable and lets release notes
group entries. The accepted types below match the titles this repository already
uses.

The workflow that runs this tool is advisory: it marks the step
``continue-on-error``, so a non-conforming title is reported without blocking the
pull request. Read the title from ``PR_TITLE`` or pass it as the only argument.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

TITLE_TYPES = (
    "build",
    "chore",
    "ci",
    "docs",
    "feat",
    "fix",
    "perf",
    "refactor",
    "revert",
    "security",
    "style",
    "test",
)
MAX_TITLE_LENGTH = 100
_PATTERN = re.compile(rf"^(?:{'|'.join(TITLE_TYPES)})(?:\([a-z0-9._/-]+\))?!?: \S.*$")


def title_errors(title: str) -> list[str]:
    """Return every convention violation for one pull-request title."""
    errors: list[str] = []
    stripped = title.strip()
    if not stripped:
        return ["title is empty"]
    if stripped != title:
        errors.append("title has leading or trailing whitespace")
    if len(stripped) > MAX_TITLE_LENGTH:
        errors.append(f"title is {len(stripped)} characters; keep it at or below {MAX_TITLE_LENGTH}")
    if not _PATTERN.match(stripped):
        errors.append("title must start with a known type, for example 'fix: ...' or 'feat(miniapp): ...'")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("title", nargs="?", help="Pull-request title; defaults to the PR_TITLE environment variable")
    arguments = parser.parse_args(argv)
    title = arguments.title if arguments.title is not None else os.environ.get("PR_TITLE", "")
    errors = title_errors(title)
    if not errors:
        print(f"Pull-request title follows the repository convention: {title!r}")
        return 0
    print(f"Pull-request title does not follow the repository convention: {title!r}", file=sys.stderr)
    for error in errors:
        print(f" - {error}", file=sys.stderr)
    print(f"Accepted types: {', '.join(TITLE_TYPES)}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
