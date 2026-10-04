"""Bounded versioned Director brief for a separate Story-model epilogue."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class EpilogueBrief:
    schema_version: int
    expected_revision: int
    time_scope: str
    cover: tuple[str, ...]
    do_not_invent: tuple[str, ...]
    pov: str


def _items(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 8:
        raise ValueError("An epilogue brief has at most eight items per section")
    if any(not isinstance(item, str) or not item.strip() or len(item) > 400 for item in value):
        raise ValueError("Epilogue brief items must be short nonempty text")
    return tuple(item.strip() for item in value)


def parse_epilogue_brief(raw: str, *, expected_revision: int, pov: str) -> EpilogueBrief:
    if not isinstance(raw, str) or len(raw.encode()) > 16000:
        raise ValueError("Epilogue brief exceeds the supported bound")
    data = json.loads(raw)
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("Unsupported epilogue brief version")
    if type(data.get("expected_revision")) is not int or data["expected_revision"] != expected_revision:
        raise ValueError("The epilogue brief used a stale story revision")
    scope = data.get("time_scope")
    if not isinstance(scope, str) or not scope.strip() or len(scope) > 300 or data.get("pov") != pov:
        raise ValueError("Epilogue time scope or point of view is invalid")
    return EpilogueBrief(
        1, expected_revision, scope.strip(), _items(data.get("cover")), _items(data.get("do_not_invent")), pov
    )
