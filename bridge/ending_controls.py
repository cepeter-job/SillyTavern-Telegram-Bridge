"""Revision-bound ending preferences, finale consent and read-only saved prose."""

from __future__ import annotations

import sqlite3
from typing import Any

from bridge.director_room import director_room
from bridge.ending_service import load_ending_state
from bridge.narrative_checkpoints import NarrativeCheckpoint, create_pre_finale_checkpoint_and_enter
from bridge.narrative_repository import load_narrative_clock
from bridge.narrative_settings import (
    load_session_narrative_settings,
    normalize_narrative_settings,
    save_session_narrative_settings,
)
from bridge.sqlite_store import write_transaction
from bridge.transcript_repository import story_row_by_id


def require_ending_revision(db: sqlite3.Connection, chat_id: str, session_id: str, revision: str) -> dict[str, Any]:
    view = director_room(db, chat_id, session_id)
    if not isinstance(revision, str) or view["revision"] != revision:
        raise ValueError("This story or its ending settings changed. Refresh Director Room.")
    return view


def configure_ending(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    revision: str,
    *,
    mode: object,
    require_confirmation: object,
) -> None:
    if (
        not isinstance(mode, str)
        or mode not in {"open_ended", "closed_story"}
        or type(require_confirmation) is not bool
    ):
        raise ValueError("Choose Open-ended or Closed Story and an explicit confirmation preference.")
    with write_transaction(db):
        view = require_ending_revision(db, chat_id, session_id, revision)
        if not view["ending"]["editable"]:
            raise ValueError("Ending settings are locked once the finale begins.")
        current = load_session_narrative_settings(db, chat_id, session_id)
        changed = normalize_narrative_settings(
            current.to_dict()
            | {
                "preset": "custom",
                "ending_mode": mode,
                "require_finale_confirmation": require_confirmation,
            }
        )
        save_session_narrative_settings(db, chat_id, session_id, changed)


def confirm_finale(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    revision: str,
    *,
    operation_id: str,
) -> NarrativeCheckpoint:
    with write_transaction(db):
        view = require_ending_revision(db, chat_id, session_id, revision)
        if view["ending"]["lifecycle"] != "finale_ready":
            raise ValueError("The current story is not ready for its finale. Reassess in Director Room.")
        clock = load_narrative_clock(db, chat_id, session_id)
        if clock is None:
            raise ValueError("This story no longer exists.")
        ending = load_ending_state(db, chat_id, session_id)
        return create_pre_finale_checkpoint_and_enter(
            db,
            chat_id,
            session_id,
            expected_story_revision=clock["state_revision"],
            expected_lifecycle_revision=ending.lifecycle_revision,
            operation_id=operation_id,
        )


def saved_ending(db: sqlite3.Connection, chat_id: str, session_id: str) -> dict[str, str]:
    """Read explicit committed identities only; no model or delivery mutation."""
    state = load_ending_state(db, chat_id, session_id)
    result = {"lifecycle": state.lifecycle, "resolution": "", "epilogue": ""}
    for key, rowid in (("resolution", state.resolution_rowid), ("epilogue", state.epilogue_committed_rowid)):
        if rowid is not None:
            row = story_row_by_id(db, chat_id, session_id, rowid)
            if row and row[0] == "assistant":
                result[key] = row[1]
    return result
