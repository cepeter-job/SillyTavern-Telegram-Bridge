"""Bounded, explicit remapping of transcript identities in an immutable snapshot."""

from __future__ import annotations

import json
from typing import Any

_ROW_KEYS = frozenset(
    {
        "rowid",
        "source_rowid",
        "start_rowid",
        "end_rowid",
        "first_seen_rowid",
        "last_seen_rowid",
        "updated_rowid",
        "updated_through_rowid",
        "covered_until_rowid",
        "source_start_rowid",
        "source_end_rowid",
        "accepted_through_rowid",
        "accepted_after_rowid",
        "ready_through_rowid",
        "through_rowid",
        "source_revision",
    }
)


def checkpoint_row_references(value: Any) -> set[int]:
    result: set[int] = set()

    def walk(item: Any, depth: int = 0) -> None:
        if depth > 32:
            raise ValueError("Checkpoint nesting exceeds the restoration limit")
        if isinstance(item, dict):
            for key, part in item.items():
                if key in _ROW_KEYS and part is not None:
                    if type(part) is not int or part < 0:
                        raise ValueError("Checkpoint contains an invalid transcript reference")
                    if part:
                        result.add(part)
                elif key in {"evidence_json", "resolution_evidence_json"} and isinstance(part, str) and part:
                    walk(json.loads(part), depth + 1)
                else:
                    walk(part, depth + 1)
        elif isinstance(item, list):
            for part in item:
                walk(part, depth + 1)

    walk(value)
    if len(result) > 20000:
        raise ValueError("Too many checkpoint references for bounded restoration")
    return result


def remap_checkpoint(value: Any, rowids: dict[int, int], depth: int = 0) -> Any:
    if depth > 32:
        raise ValueError("Checkpoint nesting exceeds the restoration limit")
    if isinstance(value, list):
        return [remap_checkpoint(item, rowids, depth + 1) for item in value]
    if not isinstance(value, dict):
        return value
    result: dict[str, Any] = {}
    for key, part in value.items():
        if key in _ROW_KEYS and part is not None:
            if part not in rowids:
                raise ValueError("A checkpoint reference is outside its saved transcript")
            result[key] = rowids[part]
        elif key in {"evidence_json", "resolution_evidence_json"} and isinstance(part, str) and part:
            result[key] = json.dumps(
                remap_checkpoint(json.loads(part), rowids, depth + 1), ensure_ascii=False, separators=(",", ":")
            )
        else:
            result[key] = remap_checkpoint(part, rowids, depth + 1)
    return result
