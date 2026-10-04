"""Ending plans and lifecycle rules, separate from Story prose and delivery."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import asdict, replace
from typing import Any

from bridge.director_repository import invalidate_ai_direction
from bridge.ending_repository import (
    append_ending_goal_revision,
    list_ending_goal_revisions,
    load_ending_row,
    save_ending_row,
)
from bridge.ending_values import EndingGoalRevision, EndingState, validate_ending_transition
from bridge.narrative_arc_repository import unresolved_major_arc_ids
from bridge.narrative_repository import load_narrative_clock, load_narrative_state_row
from bridge.narrative_settings import load_session_narrative_settings
from bridge.sqlite_store import write_transaction


def load_ending_state(db: sqlite3.Connection, chat_id: str, session_id: str) -> EndingState:
    row = load_ending_row(db, chat_id, session_id)
    return EndingState(**row) if row is not None else EndingState()


def ending_goal_history(
    db: sqlite3.Connection, chat_id: str, session_id: str, limit: int = 100
) -> list[EndingGoalRevision]:
    return [EndingGoalRevision(**row) for row in list_ending_goal_revisions(db, chat_id, session_id, limit=limit)]


def _revision(value: Any) -> int:
    if type(value) is not int or not 0 <= value < 2**63:
        raise ValueError("Ending revisions must be nonnegative integers")
    return value


def ending_source(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    *,
    story_revision: int,
    expected_lifecycle_revision: int,
    expected_goal_revision: int,
) -> tuple[EndingState, dict[str, Any]]:
    _revision(story_revision)
    _revision(expected_lifecycle_revision)
    _revision(expected_goal_revision)
    clock = load_narrative_clock(db, chat_id, session_id)
    current = load_ending_state(db, chat_id, session_id)
    if (
        clock is None
        or clock["state_revision"] != story_revision
        or current.lifecycle_revision != expected_lifecycle_revision
        or current.goal_revision != expected_goal_revision
    ):
        raise ValueError("The story or ending plan changed. Refresh Director Room.")
    return current, clock


def require_current_ending_facts(clock: dict[str, Any]) -> None:
    if (
        clock["invalidated_from_rowid"] is not None
        or clock["reconciled_history_revision"] != clock["history_revision"]
        or clock["updated_through_rowid"] < clock["latest_rowid"]
        or not clock["latest_rowid"]
    ):
        raise ValueError("Story continuity is still catching up; ending decisions need current committed facts.")


def without_readiness(state: EndingState) -> EndingState:
    return replace(
        state,
        lifecycle="open",
        finale_ready_revision=None,
        ready_history_revision=None,
        ready_settings_revision=None,
        ready_rewrite_revision=None,
        ready_through_rowid=None,
        readiness_reason="",
        required_arcs=(),
    )


def publish_ending_state(
    db: sqlite3.Connection, chat_id: str, session_id: str, current: EndingState, changed: EndingState
) -> EndingState:
    if changed.lifecycle != current.lifecycle:
        validate_ending_transition(current.lifecycle, changed.lifecycle)
    if not save_ending_row(
        db,
        chat_id,
        session_id,
        asdict(replace(changed, updated_at=time.time())),
        expected_revision=current.lifecycle_revision,
    ):
        raise ValueError("A newer ending decision changed this story.")
    return load_ending_state(db, chat_id, session_id)


def set_ending_goal(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    goal: str,
    *,
    source: str,
    story_revision: int,
    expected_lifecycle_revision: int,
    expected_goal_revision: int,
    scene_id: str = "",
    reason: str = "",
) -> EndingState:
    if not isinstance(goal, str) or len(goal) > 4000 or source not in ("user", "director"):
        raise ValueError("Choose an ending goal of at most 4,000 characters and a valid source.")
    if not isinstance(reason, str) or len(reason) > 1000 or (source == "director" and not reason.strip()):
        raise ValueError("An automatic ending-goal adaptation needs a short reason.")
    if not isinstance(scene_id, str) or len(scene_id) > 100:
        raise ValueError("The ending-goal scene reference is invalid.")
    with write_transaction(db):
        current, clock = ending_source(
            db,
            chat_id,
            session_id,
            story_revision=story_revision,
            expected_lifecycle_revision=expected_lifecycle_revision,
            expected_goal_revision=expected_goal_revision,
        )
        if current.lifecycle not in {"open", "finale_ready"}:
            raise ValueError("The ending goal is locked once the finale begins.")
        if source == "director":
            require_current_ending_facts(clock)
            if load_session_narrative_settings(db, chat_id, session_id).ending_mode != "closed_story":
                raise ValueError("Automatic ending-goal adaptation requires Closed Story mode.")
        goal = goal.strip()
        if current.current_goal == goal:
            return current
        changed = replace(without_readiness(current), current_goal=goal, goal_revision=current.goal_revision + 1)
        now = time.time()
        saved = publish_ending_state(db, chat_id, session_id, current, changed)
        append_ending_goal_revision(
            db,
            chat_id,
            session_id,
            {
                "goal_revision": saved.goal_revision,
                "source": source,
                "story_revision": story_revision,
                "scene_id": scene_id,
                "previous_goal": current.current_goal,
                "new_goal": goal,
                "reason": reason.strip() or "Manual ending goal",
                "created_at": now,
            },
        )
        if source == "user":
            invalidate_ai_direction(db, chat_id, session_id, now)
        return saved


def mark_finale_ready(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    *,
    story_revision: int,
    expected_lifecycle_revision: int,
    expected_goal_revision: int,
    direction_revision: int,
    reason: str,
) -> EndingState:
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
        raise ValueError("Finale readiness needs a short explanation.")
    _revision(direction_revision)
    with write_transaction(db):
        current, clock = ending_source(
            db,
            chat_id,
            session_id,
            story_revision=story_revision,
            expected_lifecycle_revision=expected_lifecycle_revision,
            expected_goal_revision=expected_goal_revision,
        )
        require_current_ending_facts(clock)
        settings = load_session_narrative_settings(db, chat_id, session_id)
        narrative = load_narrative_state_row(db, chat_id, session_id)
        if settings.ending_mode != "closed_story":
            raise ValueError("Finale readiness is available only in Closed Story mode.")
        if narrative is None or narrative["story_phase"] not in {"escalation", "climax", "resolution"}:
            raise ValueError("The committed story is not developed enough for its finale.")
        if current.lifecycle == "finale_ready":
            if current.finale_ready_revision == story_revision:
                return current
            raise ValueError("Finale readiness changed. Reassess the current story.")
        validate_ending_transition(current.lifecycle, "finale_ready")
        required = unresolved_major_arc_ids(db, chat_id, session_id, limit=129)
        if len(required) > 128:
            raise ValueError("Too many unresolved major arcs for a bounded finale; review them in Director Room.")
        changed = replace(
            current,
            lifecycle="finale_ready",
            finale_ready_revision=story_revision,
            finale_direction_revision=direction_revision,
            ready_history_revision=clock["history_revision"],
            ready_rewrite_revision=clock["rewrite_revision"],
            ready_settings_revision=clock["settings_revision"],
            ready_through_rowid=clock["latest_rowid"],
            required_arcs=tuple(required),
            readiness_reason=reason.strip(),
        )
        return publish_ending_state(db, chat_id, session_id, current, changed)


def apply_ending_proposal(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    *,
    expected: EndingState,
    story_revision: int,
    direction_revision: int,
    goal_update: str | None,
    goal_reason: str,
    finale_ready: bool | None,
    finale_reason: str,
) -> EndingState:
    """Publish validated planning changes inside the caller's Director transaction."""
    with write_transaction(db):
        current = load_ending_state(db, chat_id, session_id)
        if current != expected:
            raise ValueError("The ending plan changed while the Director was working.")
        if goal_update is not None:
            current = set_ending_goal(
                db,
                chat_id,
                session_id,
                goal_update,
                source="director",
                story_revision=story_revision,
                expected_lifecycle_revision=current.lifecycle_revision,
                expected_goal_revision=current.goal_revision,
                reason=goal_reason,
            )
        if finale_ready is True:
            current = mark_finale_ready(
                db,
                chat_id,
                session_id,
                story_revision=story_revision,
                expected_lifecycle_revision=current.lifecycle_revision,
                expected_goal_revision=current.goal_revision,
                direction_revision=direction_revision,
                reason=finale_reason,
            )
        elif finale_ready is False and current.lifecycle == "finale_ready":
            current = publish_ending_state(db, chat_id, session_id, current, without_readiness(current))
        return current
