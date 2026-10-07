"""Bounded numeric incident counters, with read-only durable-memory inspection."""

import logging
import re
import sqlite3
from collections.abc import Callable, Mapping
from contextlib import closing
from pathlib import Path

from bridge.memory_queue import queue_counters


def safe_counter_values(*callbacks: Callable[[], Mapping[str, object]] | None) -> dict[str, bool | int]:
    result: dict[str, bool | int] = {}
    for callback in callbacks:
        if callback is None:
            continue
        try:
            values = callback()
        except Exception:
            logging.warning("Memory diagnostics safe counters unavailable")
            continue
        if not isinstance(values, Mapping):
            continue
        for key, value in values.items():
            if len(result) >= 32:
                break
            if not isinstance(key, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", key) is None:
                continue
            if type(value) is bool:
                result[key] = value
            elif type(value) is int and -(2**63) <= value <= 2**63 - 1:
                result[key] = value
    return result


def memory_queue_counters(database_path: Path) -> dict[str, bool | int]:
    """Incident-only query: no schema initialization, lease recovery, or file creation."""
    uri = Path(database_path).resolve().as_uri() + "?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=0.1)) as db:
            return dict(queue_counters(db))
    except sqlite3.Error:
        return {"memory.jobs.available": False}
