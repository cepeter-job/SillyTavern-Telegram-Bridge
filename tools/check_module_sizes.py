"""Ratchet existing Python file-size debt without imposing artificial minimum sizes."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path

LIMIT = 500
BASELINE = "tools/module_size_baseline.json"


def python_paths(root: Path) -> list[Path]:
    paths = list(root.glob("*.py"))
    for name in ("bridge", "tests", "tools"):
        paths.extend((root / name).rglob("*.py"))
    return sorted(path for path in paths if not {"__pycache__", "node_modules", ".venv"}.intersection(path.parts))


def measure_sizes(root: Path) -> dict[str, int]:
    return {
        path.relative_to(root).as_posix(): len(path.read_text(encoding="utf-8").splitlines())
        for path in python_paths(root)
    }


def size_errors(
    sizes: dict[str, int],
    baseline: dict[str, int],
    *,
    previous: dict[str, int] | None = None,
) -> list[str]:
    """Require exact, shrinking exceptions; never accept a newly increased cap."""
    errors = []
    for path, count in sorted(sizes.items()):
        maximum = baseline.get(path, LIMIT)
        if count > maximum:
            errors.append(f"{path}: {count} lines exceeds allowed {maximum}")
    for path, maximum in sorted(baseline.items()):
        count = sizes.get(path)
        if count is None or count <= LIMIT:
            errors.append(f"{path}: remove obsolete size exception")
        elif count < maximum:
            errors.append(f"{path}: lower exception from {maximum} to {count}")
        if previous is not None and maximum > previous.get(path, LIMIT):
            errors.append(f"{path}: exception increases the reviewed base limit")
    return errors


def _exceptions(raw: str) -> dict[str, int]:
    document = json.loads(raw)
    if not isinstance(document, dict) or document.get("version") != 1 or document.get("max_lines") != LIMIT:
        raise ValueError("Unsupported module-size baseline format")
    result = document.get("exceptions")
    if not isinstance(result, dict):
        raise ValueError("Module-size exceptions must be an object")
    for name, count in result.items():
        if (
            not isinstance(name, str)
            or not name.endswith(".py")
            or Path(name).is_absolute()
            or ".." in Path(name).parts
            or type(count) is not int
            or count <= LIMIT
        ):
            raise ValueError("Module-size exceptions must be repository Python paths with caps above 500")
    return result


def _git_file(root: Path, ref: str, path: str) -> str | None:
    result = subprocess.run(  # noqa: S603 -- fixed local git read, validated revision and repository path
        [shutil.which("git") or "/usr/bin/git", "show", f"{ref}:{path}"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode:
        return None
    return result.stdout


def previous_limits(root: Path, ref: str, baseline: dict[str, int]) -> dict[str, int]:
    if not re.fullmatch(r"[0-9a-f]{40,64}", ref) or not ref.strip("0"):
        raise ValueError("Base revision must be a full nonzero commit SHA")
    subprocess.run(  # noqa: S603 -- fixed local git read with validated SHA
        [shutil.which("git") or "/usr/bin/git", "cat-file", "-e", f"{ref}^{{commit}}"],
        cwd=root,
        check=True,
        capture_output=True,
        timeout=15,
    )
    raw = _git_file(root, ref, BASELINE)
    if raw is not None:
        return _exceptions(raw)
    # Bootstrap once against measured files in the reviewed parent, not today's caps.
    result = {}
    for path in baseline:
        source = _git_file(root, ref, path)
        if source is not None and len(source.splitlines()) > LIMIT:
            result[path] = len(source.splitlines())
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--base-ref", help="Full reviewed PR-base or push-before SHA; prevents raising exceptions")
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        baseline = _exceptions((root / BASELINE).read_text(encoding="utf-8"))
        previous = previous_limits(root, args.base_ref, baseline) if args.base_ref else None
        sizes = measure_sizes(root)
        errors = size_errors(sizes, baseline, previous=previous)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Module-size gate failed: {exc}")
        return 1
    if errors:
        print("\n".join(errors))
        return 1
    print(f"Module sizes: {len(sizes)} files, {len(baseline)} shrinking exceptions, new-file maximum {LIMIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
