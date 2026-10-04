"""Read-only narrative context; stale facts never masquerade as the current scene."""

from __future__ import annotations

import sqlite3
from typing import Any

from bridge.director_guidance import director_guidance_for_session
from bridge.ending_repository import load_ending_row
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


def narrative_context_for_session(
    db: sqlite3.Connection, chat_id: str, session_id: str, consumer: str, *, through_rowid: int | None = None
) -> str:
    renderers = {"story": story_policy_text, "choices": choice_policy_text, "group": group_policy_text}
    if consumer not in renderers:
        raise ValueError("Unknown narrative context consumer")
    policy = narrative_policy(load_session_narrative_settings(db, chat_id, session_id))
    clock = load_narrative_clock(db, chat_id, session_id)
    current = narrative_clock_is_current(clock)
    if through_rowid is not None and clock is not None and clock["updated_through_rowid"] > through_rowid:
        current = False
    state = load_narrative_state(db, chat_id, session_id) if current else None
    text = renderers[consumer](policy, state)
    guidance = director_guidance_for_session(db, chat_id, session_id, through_rowid=through_rowid)
    ending = load_ending_row(db, chat_id, session_id)
    if ending is not None and ending["lifecycle"] == "finale" and consumer == "story":
        text += (
            "\n\nFinale phase: resolve the established dramatic conflicts through plausible committed events. "
            "The finale may take several turns; do not force the user's decisions to reach an ending. "
            "Do not write the separate epilogue in this response or declare the session closed. "
            "The bridge commissions a separate epilogue only after the actual resolution is reconciled."
        )
    return text + ("\n\n" + guidance if guidance else "")


def narrative_choice_is_steering(db: sqlite3.Connection, chat_id: str, session_id: str) -> bool:
    """A current off-screen scene is steering; world-focused unknown scenes stay conservative."""
    clock = load_narrative_clock(db, chat_id, session_id)
    if narrative_clock_is_current(clock):
        present = load_narrative_state(db, chat_id, session_id).user_present
        if present is not None:
            return not present
    settings = load_session_narrative_settings(db, chat_id, session_id)
    return settings.scene_focus != "user" or settings.offscreen_policy == "free"
