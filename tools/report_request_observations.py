#!/usr/bin/env python3
"""Summarize explicitly selected content-free request-observation JSON logs."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from collections import deque
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import BinaryIO

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bridge.request_observation import PHASES, PURPOSES, TRANSPORTS, usage_summary  # noqa: E402
from tools.profile_prompt_prefix import write_report  # noqa: E402

MAX_LINE = 65536
MAX_BYTES = 32_000_000
MAX_RECORDS = 100000
COUNTS = frozenset(
    {
        "input_tokens",
        "cached_tokens",
        "output_tokens",
        "elapsed_ms",
        "bytes",
        "message_count",
        "usage_readings",
        "observation_ordinal",
        "attempt",
        "stable_prefix_characters",
        "profile_sample_count",
        "instruction_duplicate_groups",
        "instruction_characters",
        "user_characters",
        "assistant_characters",
        "tool_characters",
    }
)
ENUMS = {
    "purpose": PURPOSES,
    "phase": PHASES,
    "transport": TRANSPORTS,
    "status": frozenset({"failed", "completed"}),
    "prefix_scope": frozenset({"native_prompt", "unavailable", "profile_unavailable"}),
    "section_attribution": frozenset({"wire_roles_only", "unavailable"}),
}


def _safe_record(value: object) -> dict | None:
    if not isinstance(value, dict) or value.get("event") != "provider.request_observed":
        return None
    identifier = value.get("profile_request_id")
    if not isinstance(identifier, str) or not re.fullmatch(r"wire-[0-9a-f]{24}", identifier):
        return None
    result: dict = {"profile_request_id": identifier}
    for key in COUNTS:
        item = value.get(key)
        result[key] = item if type(item) is int and 0 <= item <= 1_000_000_000 else None
    for key, allowed in ENUMS.items():
        item = value.get(key)
        result[key] = item if isinstance(item, str) and item in allowed else "unknown"
    for key in ("model_ref", "provider_ref"):
        item = value.get(key)
        if isinstance(item, str) and re.fullmatch(r"[0-9a-f]{64}", item):
            result[key] = item
    inputs, cached = result["input_tokens"], result["cached_tokens"]
    if cached is not None and (inputs is None or cached > inputs):
        result["cached_tokens"] = None
    result["usage_complete"] = (
        value.get("usage_complete") is True
        and inputs is not None
        and result["output_tokens"] is not None
        and result["status"] == "completed"
    )
    for key in ("prefix_observed", "non_text_payload_present"):
        result[key] = value.get(key) is True
    result["full_prompt_token_count_known"] = False
    return result


def summarize(events: Iterable[object], *, window: int = 128) -> dict:
    if type(window) is not int or not 1 <= window <= 256:
        raise ValueError("observation_window_limit")
    recent: deque[dict] = deque(maxlen=window)
    seen = 0
    for index, value in enumerate(events):
        if index >= MAX_RECORDS:
            raise ValueError("observation_input_record_limit")
        record = _safe_record(value)
        if record is not None:
            seen += 1
            recent.append(record)
    records = list(recent)
    groups: dict[tuple, list[dict]] = {}
    for record in records:
        key = tuple(record.get(k) for k in ("purpose", "model_ref", "provider_ref", "phase", "transport"))
        groups.setdefault(key, []).append(record)
    return {
        "schema_version": 1,
        "type": "request_observation_log_report",
        "recent_window": window,
        "records_seen": seen,
        "records": records,
        "window_usage": usage_summary(records),
        "groups": [
            {
                **dict(zip(("purpose", "model_ref", "provider_ref", "phase", "transport"), key, strict=True)),
                "usage": usage_summary(rows),
            }
            for key, rows in groups.items()
        ],
        "optimization_authorized": False,
        "accepted_work_savings_fraction": None,
        "limitations": [
            "This window includes recorded generation attempts, not embeddings or OAuth refreshes.",
            "Missing, disabled, rotated, or dropped observations prevent complete traffic accounting.",
            "Each input log event is counted once; do not concatenate overlapping copies of the same log.",
            "Transport completion does not establish delivery, accepted output, narrative quality, or lower cost.",
            "Prefix stability is a textual observation, not a prediction of cache hits.",
        ],
    }


def _unique(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("observation_duplicate_key")
        result[key] = value
    return result


def _records(stream: BinaryIO) -> Iterator[dict]:
    total = 0
    while line := stream.readline(MAX_LINE + 1):
        total += len(line)
        if len(line) > MAX_LINE or total > MAX_BYTES:
            raise ValueError("observation_input_size_limit")
        if line.strip():
            yield json.loads(line, object_pairs_hook=_unique)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--window", type=int, default=128)
    args = parser.parse_args(argv)
    try:
        descriptor = os.open(args.log, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("observation_requires_regular_file")
            report = summarize(_records(stream), window=args.window)
        write_report(args.output, report)
    except (OSError, ValueError, TypeError, RecursionError):
        print(
            "Observation report failed: input invalid, unavailable, oversized, or output already exists.",
            file=sys.stderr,
        )
        return 1
    print(json.dumps({"status": "reported", "records_seen": report["records_seen"], "optimization_authorized": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
