#!/usr/bin/env python3
"""Offline matched-Python benchmark of real bridge text paths using synthetic data.

No provider calls, production databases, environment secrets, or prompt exports.
Run the same code revision on an idle host under both Python interpreters.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import resource
import statistics
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bridge.card_content import card_fields  # noqa: E402
from bridge.context_compaction import estimate_message_tokens  # noqa: E402
from bridge.generation import build_chat_messages  # noqa: E402
from bridge.settings import load_app_settings  # noqa: E402
from bridge.telegram_output import telegram_format_spans  # noqa: E402

SCHEMA = 1
SOURCE_PATHS = (
    "tools/benchmark_python_runtime.py",
    "bridge/generation.py",
    "bridge/card_content.py",
    "bridge/context_compaction.py",
    "bridge/telegram_output.py",
)
FORMATTING_SAMPLE = (
    "<b>Rowan</b>: The old station is quiet.\n"
    "<i>The signal lantern blinks twice.</i>\n"
    "You open the journal without deciding anyone else's actions.\n"
) * 8


class _NoPersona:
    def name(self, _persona: str) -> str:
        return ""

    def get(self, _persona: str) -> None:
        return None


def _percentile(samples: list[float], quantile: float) -> float:
    """Nearest-rank percentile (not interpolated)."""
    if not samples:
        raise ValueError("empty samples")
    return sorted(samples)[max(0, math.ceil(len(samples) * quantile) - 1)]


def measure_case(action: Callable[[], str], *, iterations: int, warmup: int) -> dict:
    """Measure CPU and wall time, rejecting changes in synthetic output."""
    if not (5 <= iterations <= 5000) or not (0 <= warmup <= 500):
        raise ValueError("iterations/warmup out of bounds")
    digest: str | None = None
    for _ in range(warmup):
        action()
    wall_ms: list[float] = []
    cpu_ms: list[float] = []
    for _ in range(iterations):
        cpu_start = time.process_time_ns()
        wall_start = time.perf_counter_ns()
        payload = action()
        elapsed_wall = (time.perf_counter_ns() - wall_start) / 1_000_000
        elapsed_cpu = (time.process_time_ns() - cpu_start) / 1_000_000
        current = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        if digest is not None and current != digest:
            raise ValueError("non-deterministic synthetic output")
        digest = current
        wall_ms.append(elapsed_wall)
        cpu_ms.append(elapsed_cpu)
    return {
        "samples": iterations,
        "semantic_sha256": digest,
        "wall_ms_p50": round(statistics.median(wall_ms), 6),
        "wall_ms_p95": round(_percentile(wall_ms, 0.95), 6),
        "cpu_ms_p50": round(statistics.median(cpu_ms), 6),
        "cpu_ms_p95": round(_percentile(cpu_ms, 0.95), 6),
    }


def _fingerprint_sources() -> str:
    digest = hashlib.sha256()
    for path in SOURCE_PATHS:
        digest.update(path.encode("utf-8"))
        digest.update((ROOT / path).read_bytes())
    return digest.hexdigest()


def _jit_metadata() -> dict[str, bool]:
    jit = getattr(sys, "_jit", None)

    def check(method: str) -> bool:
        function = getattr(jit, method, None)
        try:
            return bool(function()) if callable(function) else False
        except Exception:
            return False

    return {"available": check("is_available"), "enabled": check("is_enabled")}


def run_benchmark(*, iterations: int, warmup: int) -> dict:
    """Build all fixtures from synthetic inputs, isolated from user configuration."""
    with tempfile.TemporaryDirectory(prefix="sttb-runtime-benchmark-") as directory:
        settings = load_app_settings({}, home=Path(directory))
        fields = card_fields(
            {
                "name": "Rowan",
                "description": "A station keeper who maintains causal story continuity. " * 10,
                "system_prompt": "Respect character knowledge boundaries and reader agency.",
                "post_history_instructions": "Preserve established events without retcons.",
            },
            app_settings=settings,
        )
        session = {
            "persona_id": "",
            "world_file": "",
            "model_id": "synthetic",
            "author_note": "The journal is sealed.",
        }
        history = [
            (
                "user" if i % 2 == 0 else "assistant",
                f"Turn {i}: The signal lantern remains behind the locked gate. " * 3,
            )
            for i in range(40)
        ]
        persona = _NoPersona()

        def prompt(*, compact: bool) -> str:
            messages = build_chat_messages(
                session,
                fields,
                "Inspect the lantern without controlling Rowan.",
                history,
                persona_service=persona,
                session_summary="The journal was sealed. The gate remains closed.",
                scene_context="Rowan watches from the station platform.",
                app_settings=settings,
                defer_compaction=not compact,
            )
            estimate = estimate_message_tokens(messages)
            return json.dumps(messages, sort_keys=True, ensure_ascii=False, default=str) + str(estimate)

        def telegram() -> str:
            visible, spans = telegram_format_spans(FORMATTING_SAMPLE)
            return visible + repr(spans)

        cases = {
            "prompt_assembly": lambda: prompt(compact=False),
            "prompt_compaction": lambda: prompt(compact=True),
            "telegram_format": telegram,
        }
        workloads = {key: measure_case(action, iterations=iterations, warmup=warmup) for key, action in cases.items()}

    maximum_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports KiB; macOS reports bytes.
    rss_kib = maximum_rss / 1024 if sys.platform == "darwin" else maximum_rss
    return {
        "schema_version": SCHEMA,
        "source_sha256": _fingerprint_sources(),
        "python": {
            "version": platform.python_version(),
            "major_minor": [sys.version_info.major, sys.version_info.minor],
            "implementation": platform.python_implementation(),
        },
        "host_class": {
            "system": platform.system(),
            "machine": platform.machine(),
            "kernel_release": platform.release(),
        },
        "jit": _jit_metadata(),
        "peak_process_rss_kib": int(rss_kib),
        "workloads": workloads,
        "provider_requests_issued": 0,
        "production_database_writes": 0,
        "raw_prompts_exported": False,
        "production_upgrade_authorized": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--warmup", type=int, default=30)
    parser.add_argument("--output", type=Path, help="New metadata-only JSON file; cannot replace existing files")
    args = parser.parse_args(argv)
    try:
        result = run_benchmark(iterations=args.iterations, warmup=args.warmup)
    except ValueError as exc:
        parser.error(str(exc))
    payload = json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n"
    if args.output:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(args.output, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(payload)
        print(json.dumps({"status": "written", "workloads": list(result["workloads"]), "provider_calls": 0}))
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
