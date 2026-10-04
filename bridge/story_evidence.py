"""Bounded references to committed prose, not model plans or provider metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class StoryEvidence:
    rowid: int
    quote: str


def parse_story_evidence(value: Any, *, maximum: int = 4) -> tuple[StoryEvidence, ...]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError("Story evidence must be a bounded list of committed-row quotations")
    result = []
    seen = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Story evidence must identify a committed row and an exact quotation")
        rowid, quote = item.get("rowid"), item.get("quote")
        if type(rowid) is not int or not 0 < rowid < 2**63:
            raise ValueError("Story evidence row identifiers must be positive integers")
        if not isinstance(quote, str) or not quote.strip() or len(quote) > 500:
            raise ValueError("Story evidence quotations must contain 1–500 characters")
        if (rowid, quote) in seen:
            raise ValueError("Story evidence cannot repeat an identical citation")
        seen.add((rowid, quote))
        result.append(StoryEvidence(rowid, quote))
    return tuple(result)
