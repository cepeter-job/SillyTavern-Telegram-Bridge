"""Bounded arc values and committed-story evidence checks shared by reconciliation."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from bridge.extraction_validation import ExtractionValidationError
from bridge.narrative_arc_repository import list_arc_rows
from bridge.story_evidence import StoryEvidence, parse_story_evidence
from bridge.transcript_repository import story_row_by_id

ARC_STATUSES = frozenset({"planned", "active", "dormant", "resolved", "abandoned"})
ARC_PHASES = frozenset({"setup", "development", "escalation", "climax", "resolution"})


@dataclass(frozen=True, slots=True)
class NarrativeArc:
    arc_id: str
    title: str
    status: str = "planned"
    phase: str = "development"
    importance: str = "minor"
    summary: str = ""
    open_questions: tuple[str, ...] = ()
    related_threads: tuple[str, ...] = ()
    source_revision: int = 0
    evidence: tuple[StoryEvidence, ...] = ()


def _text(value: Any, maximum: int, *, field: str, identifier: bool = False, required: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        code = "invalid_type" if not isinstance(value, str) else "too_long" if len(value) > maximum else "empty"
        raise ExtractionValidationError("Arc text is invalid or oversized", field, code)
    if identifier and not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise ExtractionValidationError(
            "Arc identifiers must use letters, numbers, underscore or hyphen", field, "invalid_identifier"
        )
    return value.strip()


def _strings(value: Any, maximum: int, *, field: str, identifier: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 8:
        raise ExtractionValidationError(
            "Arc collections are limited to eight items",
            field,
            "too_many_items" if isinstance(value, list) else "invalid_type",
        )
    items = tuple(_text(item, maximum, field=field, identifier=identifier, required=True) for item in value)
    if len(set(items)) != len(items):
        raise ExtractionValidationError("Arc collections cannot repeat identical items", field, "duplicate")
    return items


def parse_arc_updates(value: Any) -> tuple[NarrativeArc, ...]:
    if not isinstance(value, list) or len(value) > 8:
        raise ExtractionValidationError(
            "Reconciliation accepts at most eight arc updates",
            "arcs",
            "too_many_items" if isinstance(value, list) else "invalid_type",
        )
    result = []
    seen = set()
    for item in value:
        if not isinstance(item, dict):
            raise ExtractionValidationError("Arc updates must be objects", "arcs", "invalid_type")
        identifier = _text(item.get("arc_id"), 100, field="arcs.arc_id", identifier=True)
        status, phase, importance = item.get("status"), item.get("phase"), item.get("importance")
        for field, selected, allowed in (
            ("arcs.status", status, ARC_STATUSES),
            ("arcs.phase", phase, ARC_PHASES),
            ("arcs.importance", importance, ("major", "minor")),
        ):
            if not isinstance(selected, str) or selected not in allowed:
                code = "invalid_enum" if isinstance(selected, str) else "invalid_type"
                raise ExtractionValidationError("Arc status, phase or importance is invalid", field, code)
        if identifier in seen:
            raise ExtractionValidationError("Arc updates cannot repeat an arc identifier", "arcs.arc_id", "duplicate")
        for field_name, maximum in (("open_questions", 4000), ("related_threads", 4000), ("evidence", 4096)):
            if len(json.dumps(item.get(field_name, []), ensure_ascii=False, separators=(",", ":"))) > maximum:
                raise ExtractionValidationError(
                    "Arc collection exceeds its encoded storage bound", "arcs." + field_name, "storage_limit"
                )
        seen.add(identifier)
        result.append(
            NarrativeArc(
                identifier,
                _text(item.get("title"), 200, field="arcs.title", required=True),
                status,
                phase,
                importance,
                _text(item.get("summary", ""), 2000, field="arcs.summary"),
                _strings(item.get("open_questions", []), 400, field="arcs.open_questions"),
                _strings(item.get("related_threads", []), 100, field="arcs.related_threads", identifier=True),
                evidence=parse_story_evidence(item.get("evidence", []), field="arcs.evidence"),
            )
        )
    return tuple(result)


def load_arcs(
    db: sqlite3.Connection, chat_id: str, session_id: str, *, limit: int = 32, offset: int = 0
) -> list[NarrativeArc]:
    arcs = []
    for row in list_arc_rows(db, chat_id, session_id, limit=limit, offset=offset):
        evidence = parse_story_evidence(row["evidence"])
        arcs.append(
            NarrativeArc(
                **(
                    row
                    | {
                        "open_questions": tuple(row["open_questions"]),
                        "related_threads": tuple(row["related_threads"]),
                        "evidence": evidence,
                    }
                )
            )
        )
    return arcs


def validate_story_evidence(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    evidence: tuple[StoryEvidence, ...],
    *,
    first_rowid: int,
    through_rowid: int,
    assistant_only: bool = False,
) -> None:
    if not evidence:
        raise ValueError("An outcome needs evidence from committed story text")
    for item in evidence:
        if not first_rowid <= item.rowid <= through_rowid:
            raise ValueError("Outcome evidence must belong to the current committed reconciliation boundary")
        row = story_row_by_id(db, chat_id, session_id, item.rowid)
        if (
            row is None
            or row[0] not in ({"assistant"} if assistant_only else {"user", "assistant"})
            or item.quote not in row[1]
        ):
            raise ValueError("Outcome evidence must quote actual committed prose in this session")
