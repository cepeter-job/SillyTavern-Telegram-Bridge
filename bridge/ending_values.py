"""Immutable ending values and deterministic lifecycle edges."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise

ENDING_STATES = (
    "open",
    "finale_ready",
    "finale",
    "resolution_committed",
    "epilogue_pending",
    "epilogue_committed",
    "closed",
)
CLOSING_STATES = frozenset({"resolution_committed", "epilogue_pending", "epilogue_committed", "closed"})


@dataclass(frozen=True, slots=True)
class EndingState:
    lifecycle: str = "open"
    lifecycle_revision: int = 0
    current_goal: str = ""
    goal_revision: int = 0
    finale_ready_revision: int | None = None
    finale_direction_revision: int | None = None
    checkpoint_id: str | None = None
    finale_operation_id: str | None = None
    finale_committed_rowid: int | None = None
    resolution_rowid: int | None = None
    epilogue_operation_id: str | None = None
    epilogue_committed_rowid: int | None = None
    updated_at: float = 0.0
    ready_history_revision: int | None = None
    ready_settings_revision: int | None = None
    ready_rewrite_revision: int | None = None
    ready_through_rowid: int | None = None
    readiness_reason: str = ""
    required_arcs: tuple[str, ...] = ()
    resolution_evidence_json: str = "[]"
    epilogue_brief_json: str = ""
    work_token: str = ""
    work_started_at: float = 0.0
    work_stage: str = ""
    last_error: str = ""
    last_attempt_at: float = 0.0


@dataclass(frozen=True, slots=True)
class EndingGoalRevision:
    goal_revision: int
    source: str
    story_revision: int
    scene_id: str
    previous_goal: str
    new_goal: str
    reason: str
    created_at: float


def validate_ending_transition(before: str, after: str) -> None:
    edges = set(pairwise(ENDING_STATES)) | {("finale_ready", "open")}
    if (before, after) not in edges:
        raise ValueError("This ending lifecycle transition is not permitted")
