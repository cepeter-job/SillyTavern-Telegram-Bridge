#!/usr/bin/env python3
"""Benchmark synthetic bridge work without network, credentials or production data.

Metrics compare local interpreter CPU work, NOT total Telegram/model latency.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import resource
import sqlite3
import statistics
import sys
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bridge.context_section_metrics import estimate_context_sections  # noqa: E402
from bridge.context_selection import select_memory_blocks  # noqa: E402
from bridge.memory_contracts import MemoryBlock, MemoryBlockLeaf, MemoryEvidence, MemoryReadScope  # noqa: E402

WORKLOAD_VERSION = "bridge-synthetic-v1"


def synthetic_cases() -> tuple[dict[str, Callable[[], object]], Callable[[], None]]:
    """Construct isolated and deterministic request-shaped workloads."""
    scope = MemoryReadScope("benchmark", "synthetic-story", 1.0, 80, 2, ("mira",))
    blocks = []
    for index in range(80):
        identity = index % 40 + 1
        line = f"Fact {identity}: Mira promised to return the key at dawn."
        evidence = MemoryEvidence(identity, f"source-{identity}", identity, identity, 0, len(line))
        leaf = MemoryBlockLeaf(line, evidence, scope, "restricted", ("mira",), 2)
        blocks.append(MemoryBlock(line, (evidence,), "recall", (leaf,)))
    memory_blocks = tuple(blocks)
    sections = {
        "mandatory": ("Instruction. " * 500, "Character description. " * 200),
        "history": tuple(f"Turn {n}: consequences. " * 10 for n in range(60)),
        "world_info": ("Factions and locations. " * 320,),
        "derived": ("Previously established causal links. " * 160,),
        "task": "Continue coherently." * 20,
    }
    payload = {
        "session": "synthetic-story",
        "messages": [
            {"role": "user" if n % 2 == 0 else "assistant", "content": f"Scene {n}: " + "dialogue " * 25}
            for n in range(60)
        ],
    }
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE facts (turn INTEGER PRIMARY KEY, detail TEXT NOT NULL)")
    connection.executemany(
        "INSERT INTO facts VALUES (?, ?)",
        ((n, f"Invented event {n}") for n in range(250)),
    )

    def context_sections() -> object:
        return tuple(estimate_context_sections(sections).items())

    def memory_selection() -> object:
        result = select_memory_blocks(scope, memory_blocks)
        return result.selected_blocks, result.deduplicated_blocks, result.reason

    def json_roundtrip() -> object:
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        decoded = json.loads(encoded)
        return decoded["session"], len(decoded["messages"]), len(encoded)

    def sqlite_lookup() -> object:
        rows = connection.execute(
            "SELECT turn, detail FROM facts WHERE turn BETWEEN ? AND ? ORDER BY turn",
            (75, 155),
        ).fetchall()
        return len(rows), rows[0][0], rows[-1][0]

    return {
        "context_section_estimation": context_sections,
        "provenance_memory_dedup": memory_selection,
        "json_request_roundtrip": json_roundtrip,
        "sqlite_context_lookup": sqlite_lookup,
    }, connection.close


def percentile(values: list[float], proportion: float) -> float:
    ordered = sorted(values)
    return ordered[math.ceil(len(ordered) * proportion) - 1]


def measure(function: Callable[[], object], *, iterations: int, samples: int, warmup: int) -> dict:
    expected = function()
    for _ in range(warmup * iterations):
        if function() != expected:
            raise RuntimeError("non-deterministic benchmark fixture")
    elapsed = []
    cpu = []
    for _ in range(samples):
        start_cpu = time.process_time_ns()
        started = time.perf_counter_ns()
        last = None
        for _ in range(iterations):
            last = function()
        elapsed_ns = time.perf_counter_ns() - started
        cpu_ns = time.process_time_ns() - start_cpu
        if last != expected:
            raise RuntimeError("non-deterministic benchmark fixture")
        elapsed.append(elapsed_ns / iterations)
        cpu.append(cpu_ns / iterations)
    return {
        "output_identity": repr(expected),
        "wall_ns_per_call_median": round(statistics.median(elapsed), 1),
        "wall_ns_per_call_p95_batch": round(percentile(elapsed, 0.95), 1),
        "cpu_ns_per_call_median": round(statistics.median(cpu), 1),
    }


def jit_details() -> dict[str, bool | None]:
    implementation = getattr(sys, "_jit", None)
    result = {}
    for method in ("is_available", "is_enabled"):
        function = getattr(implementation, method, None)
        result[method] = bool(function()) if callable(function) else None
    return result


def benchmark(*, iterations: int = 100, samples: int = 15, warmup: int = 2) -> dict:
    if not (1 <= iterations <= 1000 and 3 <= samples <= 100 and 0 <= warmup <= 20):
        raise ValueError("iterations 1..1000, samples 3..100, warmup 0..20 required")
    cases, close = synthetic_cases()
    try:
        results = {
            name: measure(function, iterations=iterations, samples=samples, warmup=warmup)
            for name, function in cases.items()
        }
    finally:
        close()
    return {
        "schema_version": 1,
        "workload_version": WORKLOAD_VERSION,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "jit": jit_details(),
        "samples": samples,
        "iterations_per_sample": iterations,
        "synthetic": True,
        "provider_requests": 0,
        "cases": results,
        "peak_process_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "limitations": "Synthetic batch timings; no model or end-to-end Telegram requests.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--samples", type=int, default=15)
    parser.add_argument("--warmup", type=int, default=2)
    args = parser.parse_args(argv)
    try:
        result = benchmark(iterations=args.iterations, samples=args.samples, warmup=args.warmup)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
