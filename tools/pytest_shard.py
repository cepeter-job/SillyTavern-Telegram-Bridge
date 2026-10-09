"""Partition complete pytest discovery into deterministic, balanced whole-file shards."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import median

import pytest


def load_timings(path: str | Path) -> dict[str, tuple[float, int]]:
    """Read advisory lower-bound duration samples; never use them as a test inventory."""
    profile = json.loads(Path(path).read_text(encoding="utf-8"))
    if (
        not isinstance(profile, dict)
        or type(profile.get("schema_version")) is not int
        or profile.get("schema_version") != 1
        or not isinstance(profile.get("files"), dict)
    ):
        raise ValueError("invalid timing profile schema")
    timings = {}
    for filename, hint in profile["files"].items():
        if not isinstance(hint, dict):
            raise ValueError(f"invalid timing hint for {filename}")
        count, samples = hint.get("test_count"), hint.get("observed_minimum_seconds")
        if (
            type(count) is not int
            or count < 1
            or not isinstance(samples, list)
            or not samples
            or any(type(value) not in {int, float} for value in samples)
        ):
            raise ValueError(f"invalid timing hint for {filename}")
        try:
            numeric_samples = [float(value) for value in samples]
        except OverflowError as error:
            raise ValueError(f"invalid timing hint for {filename}: duration is too large to represent") from error
        seconds = median(numeric_samples)
        if any(value <= 0 or not math.isfinite(value) for value in numeric_samples) or not math.isfinite(seconds):
            raise ValueError(f"invalid timing hint for {filename}: durations and median must be finite and positive")
        timings[filename] = (seconds, count)
    return timings


def estimate_file_cost(filename: str, test_count: int, timings) -> float:
    if timings is None:
        return test_count
    # Unmeasured tests use a heuristic 0.1s/case, not a runtime measurement.
    seconds, reference_count = timings.get(filename, (0.0, test_count))
    # Growth scales the hint; shrinkage retains its setup/subprocess cost floor.
    cost = max(0.1 * test_count, seconds * max(1, test_count / reference_count))
    if not math.isfinite(cost):
        raise ValueError(f"non-finite timing cost for {filename} after scaling to the collected test count")
    return cost


def partition_items(items, shard_index: int, shard_count: int, timings=None):
    if shard_count < 1 or not 1 <= shard_index <= shard_count:
        raise ValueError("shard index must be between 1 and the positive shard count")
    groups = defaultdict(list)
    seen = set()
    for item in items:
        if item.nodeid in seen:
            raise ValueError(f"duplicate collected test: {item.nodeid}")
        seen.add(item.nodeid)
        groups[item.nodeid.split("::", 1)[0]].append(item)
    costs = {filename: estimate_file_cost(filename, len(group), timings) for filename, group in groups.items()}
    loads = [0.0] * shard_count
    assignments = {}
    for filename in sorted(groups, key=lambda filename: (-costs[filename], filename)):
        target = min(range(shard_count), key=lambda index: (loads[index], index))
        updated_load = loads[target] + costs[filename]
        if not math.isfinite(updated_load):
            raise ValueError(f"non-finite timing load for shard {target + 1}")
        assignments[filename] = target + 1
        loads[target] = updated_load
    return [item for item in items if assignments[item.nodeid.split("::", 1)[0]] == shard_index]


def pytest_addoption(parser):
    group = parser.getgroup("CI sharding")
    group.addoption("--shard-index", type=int, default=None, help="One-based CI shard index")
    group.addoption("--shard-count", type=int, default=4, help="Total independent CI jobs")
    group.addoption("--shard-manifest", default=None, help="Write full and selected collection evidence")
    group.addoption("--shard-timings", default=None, help="Optional checked-in file duration hints for balancing")


def pytest_collection_modifyitems(config, items):
    index = config.getoption("shard_index")
    if index is None:
        return
    count = config.getoption("shard_count")
    all_nodeids = [item.nodeid for item in items]
    try:
        timing_path = config.getoption("shard_timings")
        timings = load_timings(timing_path) if timing_path else None
        selected = partition_items(items, index, count, timings=timings)
    except (OSError, ValueError) as error:
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
