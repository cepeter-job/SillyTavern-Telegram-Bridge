"""Bounded references to committed prose, not model plans or provider metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bridge.extraction_validation import ExtractionValidationError


@dataclass(frozen=True, slots=True)
class StoryEvidence:
    rowid: int
    quote: str


def parse_story_evidence(value: Any, *, maximum: int = 4, field: str = "evidence") -> tuple[StoryEvidence, ...]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ExtractionValidationError(
            "Story evidence must be a bounded list of committed-row quotations",
            field,
            "too_many_items" if isinstance(value, list) else "invalid_type",
        )
    result = []
    seen = set()
    for item in value:
        if not isinstance(item, dict):
            raise ExtractionValidationError(
                "Story evidence must identify a committed row and an exact quotation", field, "invalid_type"
            )
        rowid, quote = item.get("rowid"), item.get("quote")
        if type(rowid) is not int or not 0 < rowid < 2**63:
            raise ExtractionValidationError(
                "Story evidence row identifiers must be positive integers",
                field + ".rowid",
                "out_of_range" if type(rowid) is int else "invalid_type",
            )
        if not isinstance(quote, str) or not quote.strip() or len(quote) > 500:
            raise ExtractionValidationError(
                "Story evidence quotations must contain 1–500 characters",
                field + ".quote",
                "invalid_type" if not isinstance(quote, str) else "empty" if not quote.strip() else "too_long",
            )
        if (rowid, quote) in seen:
            raise ExtractionValidationError("Story evidence cannot repeat an identical citation", field, "duplicate")
        seen.add((rowid, quote))
        result.append(StoryEvidence(rowid, quote))
    return tuple(result)
