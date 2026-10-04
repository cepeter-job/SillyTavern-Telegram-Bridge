"""Read-only narrative context; stale facts never masquerade as the current scene."""

from __future__ import annotations

import sqlite3
from typing import Any

from bridge.narrative_policy import choice_policy_text, group_policy_text, narrative_policy, story_policy_text
from bridge.narrative_repository import load_narrative_clock, load_narrative_state_row
from bridge.narrative_settings import load_session_narrative_settings
from bridge.narrative_values import NarrativeState


def load_narrative_state(db: sqlite3.Connection, chat_id: str, session_id: str) -> NarrativeState:
    row = load_narrative_state_row(db, chat_id, session_id)
    return NarrativeState(**row) if row else NarrativeState()


def narrative_clock_is_current(clock: dict[str, Any] | None, through_rowid: int | None = None) -> bool:
    return bool(
        clock is not None
        and clock["invalidated_from_rowid"] is None
        and clock["history_revision"] == clock["reconciled_history_revision"]
        and clock["updated_through_rowid"] >= (clock["latest_rowid"] if through_rowid is None else through_rowid)
    )


def narrative_context_for_session(db: sqlite3.Connection, chat_id: str, session_id: str, consumer: str) -> str:
    renderers = {"story": story_policy_text, "choices": choice_policy_text, "group": group_policy_text}
    if consumer not in renderers:
        raise ValueError("Unknown narrative context consumer")
    policy = narrative_policy(load_session_narrative_settings(db, chat_id, session_id))
    clock = load_narrative_clock(db, chat_id, session_id)
    state = load_narrative_state(db, chat_id, session_id) if narrative_clock_is_current(clock) else None
    return renderers[consumer](policy, state)
