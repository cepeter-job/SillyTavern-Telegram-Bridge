"""Accept a resolution only from reconciled committed Story and owned arc evidence."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, replace
from typing import Any

from bridge.ending_service import load_ending_state, publish_ending_state, require_current_ending_facts
from bridge.ending_values import EndingState
from bridge.narrative_arc_repository import load_arc_row, unresolved_major_arc_ids
from bridge.narrative_arcs import validate_story_evidence
from bridge.narrative_repository import load_narrative_clock
from bridge.story_evidence import StoryEvidence, parse_story_evidence


@dataclass(frozen=True, slots=True)
class ResolutionEvidence:
    resolved: bool
    evidence: tuple[StoryEvidence, ...] = ()


def parse_resolution_evidence(value: Any) -> ResolutionEvidence | None:
    if value is None:
        return None
    if not isinstance(value, dict) or type(value.get("resolved")) is not bool:
        raise ValueError("Resolution evidence must explicitly state whether the committed story resolved")
    evidence = parse_story_evidence(value.get("evidence", []))
    encoded = json.dumps([asdict(item) for item in evidence], ensure_ascii=False, separators=(",", ":"))
    if len(encoded) > 4096:
        raise ValueError("Resolution evidence exceeds the encoded storage bound")
    return ResolutionEvidence(value["resolved"], evidence)


def reconcile_finale_outcome(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    candidate: ResolutionEvidence | None,
    *,
    first_rowid: int,
    through_rowid: int,
    story_phase: str,
) -> EndingState:
    """Called within the accepted Narrative reconciliation transaction, after arc writes."""
    ending = load_ending_state(db, chat_id, session_id)
    if ending.lifecycle != "finale" or candidate is None or not candidate.resolved:
        return ending
    clock = load_narrative_clock(db, chat_id, session_id)
    if clock is None or through_rowid < clock["latest_rowid"]:
        return ending
    require_current_ending_facts(clock)
    if story_phase != "resolution" or ending.finale_committed_rowid is None:
        return ending
    validate_story_evidence(
        db,
        chat_id,
        session_id,
        candidate.evidence,
        first_rowid=first_rowid,
        through_rowid=through_rowid,
        assistant_only=True,
    )
    if unresolved_major_arc_ids(db, chat_id, session_id, limit=1):
        return ending
    for identifier in ending.required_arcs:
        arc = load_arc_row(db, chat_id, session_id, identifier)
        if arc is None or arc["status"] not in {"resolved", "abandoned"}:
            return ending
    if not any(item.rowid == ending.finale_committed_rowid for item in candidate.evidence):
        return ending
    return publish_ending_state(
        db,
        chat_id,
        session_id,
        ending,
        replace(
            ending,
            lifecycle="resolution_committed",
            resolution_rowid=ending.finale_committed_rowid,
            resolution_evidence_json=json.dumps(
                [asdict(item) for item in candidate.evidence], ensure_ascii=False, separators=(",", ":")
            ),
        ),
    )
