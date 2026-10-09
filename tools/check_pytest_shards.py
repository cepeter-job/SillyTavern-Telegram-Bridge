"""Fail closed unless independent CI shards execute the complete inventory exactly once."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from coverage import CoverageData
from coverage.exceptions import CoverageException


def check_manifests(root: Path, *, expected_shards: int) -> int:
    manifests = sorted(root.glob("pytest-shard-*/shard-manifest.json"))
    if expected_shards < 1 or len(manifests) != expected_shards:
        raise ValueError("missing or unexpected shard manifests")
    inventory = None
    indices = set()
    selected = Counter()
    file_owners = {}
    for path in manifests:
        data = json.loads(path.read_text(encoding="utf-8"))
        index = data["shard_index"]
        if data["shard_count"] != expected_shards or index in indices or index not in range(1, expected_shards + 1):
            raise ValueError("duplicate or invalid shard index/count")
        indices.add(index)
        discovered = data["all_nodeids"]
        if not discovered or any(not isinstance(nodeid, str) for nodeid in discovered):
            raise ValueError("invalid or empty complete inventory")
        if len(set(discovered)) != len(discovered):
            raise ValueError("duplicate tests in complete inventory")
        if inventory is None:
            inventory = discovered
        elif inventory != discovered:
            raise ValueError("shards collected different test inventories")
        for nodeid in data["selected_nodeids"]:
            if not isinstance(nodeid, str):
                raise ValueError("invalid selected test ID")
            filename = nodeid.split("::", 1)[0]
            if filename in file_owners and file_owners[filename] != index:
                raise ValueError("a test file was split across shards")
            file_owners[filename] = index
            selected[nodeid] += 1
    if inventory is None or selected != Counter(inventory):
        raise ValueError("selected tests omit or duplicate the complete inventory")
    print(f"Verified {len(inventory)} tests across {expected_shards} disjoint whole-file shards")
    return len(inventory)


def check_coverage_files(root: Path, *, expected_shards: int) -> None:
    for index in range(1, expected_shards + 1):
        path = root / f"pytest-shard-{index}" / f".coverage.shard-{index}"
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"missing or empty coverage data: {path}")
        data = CoverageData(basename=str(path))
        try:
            data.read()
            if not data.measured_files():
                raise ValueError("no measured files")
        except (ValueError, TypeError, OSError, CoverageException) as error:
            raise ValueError(f"unreadable or empty coverage data: {path}: {error}") from error
        finally:
            data.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", type=Path)
    parser.add_argument("--shards", type=int, default=4)
    args = parser.parse_args()
    try:
        check_manifests(args.reports, expected_shards=args.shards)
        check_coverage_files(args.reports, expected_shards=args.shards)
    except (ValueError, KeyError, TypeError, OSError, CoverageException) as error:
        print(f"Invalid pytest shard evidence: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
