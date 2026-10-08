#!/usr/bin/env python3
"""Inspect bounded summary backlog metrics without mutating SQLite or calling models."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bridge.summary_recovery_status import summary_recovery_snapshot  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True, help="Existing bridge SQLite file")
    parser.add_argument("--output", type=Path, help="Write a content-free JSON report")
    args = parser.parse_args(argv)
    if not args.database.is_file():
        parser.error("database must be an existing readable file")
    try:
        with closing(sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
            db.execute("PRAGMA query_only=ON")
            report = summary_recovery_snapshot(db)
    except (sqlite3.Error, ValueError, OSError):
        parser.error("unable to read a valid summary-recovery database")
    payload = json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
