#!/usr/bin/env python3
"""Profile explicitly supplied local requests without changing or sending them."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bridge.prompt_prefix_profile import PrefixProfiler  # noqa: E402
from bridge.prompt_profile_snapshot import MAX_REQUEST_BYTES  # noqa: E402
from tools.prompt_prefix_demo import demo_records  # noqa: E402

MAX_INPUT_RECORDS = 512
MAX_INPUT_BYTES = 32_000_000
MAX_LINE_BYTES = MAX_REQUEST_BYTES + 200_000


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("profile_duplicate_json_key")
        result[key] = value
    return result


def records(stream: BinaryIO) -> Iterator[dict]:
    total, count = 0, 0
    while raw := stream.readline(MAX_LINE_BYTES + 1):
        total += len(raw)
        if len(raw) > MAX_LINE_BYTES or total > MAX_INPUT_BYTES:
            raise ValueError("profile_input_size_limit")
        if not raw.strip():
            continue
        count += 1
        if count > MAX_INPUT_RECORDS:
            raise ValueError("profile_record_limit")
        record = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(record, dict) or not {"scope", "request"}.issubset(record):
            raise ValueError("profile_invalid_record")
        if set(record) - {"scope", "request", "usage"}:
            raise ValueError("profile_invalid_record")
        yield record
    if not count:
        raise ValueError("profile_empty_input")


def write_report(path: Path, report: dict) -> None:
    """Exclusive 0600 output; never replace source, a checkpoint or a symlink."""
    payload = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(payload)


def _observe(profiler: PrefixProfiler, inputs: Iterator[dict]) -> None:
    for item in inputs:
        profiler.observe(item["request"], item["scope"], usage=item.get("usage"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--samples", type=Path, help="Explicit local JSONL capture; never uploaded or rewritten")
    source.add_argument("--demo", action="store_true", help="Use synthetic native-builder cases only")
    parser.add_argument("--output", type=Path, required=True, help="New metadata-only report; refuses overwrite")
    parser.add_argument("--window", type=int, default=16)
    parser.add_argument("--max-cohorts", type=int, default=16)
    parser.add_argument("--chars-per-token", type=float, default=4.0)
    args = parser.parse_args(argv)
    try:
        profiler = PrefixProfiler(args.window, args.max_cohorts, args.chars_per_token)
        if args.demo:
            with tempfile.TemporaryDirectory(prefix="sttb-prefix-demo-") as directory:
                _observe(profiler, demo_records(Path(directory)))
        else:
            descriptor = os.open(args.samples, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                metadata = os.fstat(stream.fileno())
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_INPUT_BYTES:
                    raise ValueError("profile_input_size_or_type")
                _observe(profiler, records(stream))
        report = profiler.report()
        report.update(
            source="synthetic_native_builder" if args.demo else "explicit_local_request_capture",
            provider_requests_issued=0,
            production_database_writes=0,
            raw_prompt_exported=False,
        )
        write_report(args.output, report)
    except (OSError, ValueError, TypeError, RecursionError):
        # Parser/OS errors can contain private request text or paths. Never echo
        # exception bodies, records, arguments or source identities in diagnostics.
        print("Profile failed: check input schema, limits and exclusive output destination.", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": "profiled",
                "source": report["source"],
                "cohorts": len(report["cohorts"]),
                "observations": sum(g["observations_seen"] for g in report["cohorts"]),
                "provider_requests_issued": 0,
                "prompt_mutations": 0,
                "optimization_authorized": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
