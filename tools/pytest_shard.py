"""Partition complete pytest discovery into deterministic, balanced whole-file shards."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pytest


def partition_items(items, shard_index: int, shard_count: int):
    if shard_count < 1 or not 1 <= shard_index <= shard_count:
        raise ValueError("shard index must be between 1 and the positive shard count")
    groups = defaultdict(list)
    seen = set()
    for item in items:
        if item.nodeid in seen:
            raise ValueError(f"duplicate collected test: {item.nodeid}")
        seen.add(item.nodeid)
        groups[item.nodeid.split("::", 1)[0]].append(item)
    loads = [0] * shard_count
    assignments = {}
    for filename in sorted(groups, key=lambda filename: (-len(groups[filename]), filename)):
        target = min(range(shard_count), key=lambda index: (loads[index], index))
        assignments[filename] = target + 1
        loads[target] += len(groups[filename])
    return [item for item in items if assignments[item.nodeid.split("::", 1)[0]] == shard_index]


def pytest_addoption(parser):
    group = parser.getgroup("CI sharding")
    group.addoption("--shard-index", type=int, default=None, help="One-based CI shard index")
    group.addoption("--shard-count", type=int, default=4, help="Total independent CI jobs")
    group.addoption("--shard-manifest", default=None, help="Write full and selected collection evidence")


def pytest_collection_modifyitems(config, items):
    index = config.getoption("shard_index")
    if index is None:
        return
    count = config.getoption("shard_count")
    all_nodeids = [item.nodeid for item in items]
    try:
        selected = partition_items(items, index, count)
    except ValueError as error:
        raise pytest.UsageError(str(error)) from error
    selected_ids = {item.nodeid for item in selected}
    deselected = [item for item in items if item.nodeid not in selected_ids]
    config.hook.pytest_deselected(items=deselected)
    items[:] = selected
    manifest = config.getoption("shard_manifest")
    worker = getattr(config, "workerinput", {}).get("workerid")
    # Each xdist worker collects identically; one writer avoids corrupt concurrent JSON.
    if manifest and worker in {None, "gw0"}:
        Path(manifest).write_text(
            json.dumps(
                {
                    "shard_index": index,
                    "shard_count": count,
                    "all_nodeids": all_nodeids,
                    "selected_nodeids": [item.nodeid for item in selected],
                }
            )
            + "\n",
            encoding="utf-8",
        )
