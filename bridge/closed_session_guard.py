"""One read-only boundary for new story work after resolution or final closure."""

from __future__ import annotations

import json
import sqlite3
import time

from bridge.ending_repository import load_ending_row
from bridge.meta_repository import load_meta_value

CLOSED_STORY_MESSAGE = "This story has ended. Please start new story."
ENDING_PENDING_MESSAGE = "This story is finishing its epilogue. Use /retry to recover the saved ending."


class ClosedStoryError(ValueError):
    """No new creative content may be generated in the completed original."""


def story_mutation_message(db: sqlite3.Connection, chat_id: str, session_id: str) -> str | None:
    current = load_ending_row(db, chat_id, session_id)
    if current is None:
        return None
    if current["lifecycle"] == "closed":
        return CLOSED_STORY_MESSAGE
    if current["lifecycle"] in {"resolution_committed", "epilogue_pending", "epilogue_committed"}:
        return ENDING_PENDING_MESSAGE
    return None


def is_session_closed(db: sqlite3.Connection, chat_id: str, session_id: str) -> bool:
    return story_mutation_message(db, chat_id, session_id) == CLOSED_STORY_MESSAGE


def guard_story_mutation(db: sqlite3.Connection, chat_id: str, session_id: str) -> None:
    message = story_mutation_message(db, chat_id, session_id)
    if message is not None:
        raise ClosedStoryError(message)


def guard_ending_configuration(
    db: sqlite3.Connection, chat_id: str, session_id: str, before: dict[str, object], after: dict[str, object]
) -> None:
    guard_story_mutation(db, chat_id, session_id)
    current = load_ending_row(db, chat_id, session_id)
    if current is not None and current["lifecycle"] == "finale":
        if any(before.get(key) != after.get(key) for key in ("ending_mode", "require_finale_confirmation")):
            raise ValueError("Ending mode and finale confirmation are locked once the finale begins.")


def closed_session_allows_input(
    db: sqlite3.Connection, chat_id: str, session_id: str, actor_id: str, command: str
) -> bool:
    """Only inspection, explicit ending recovery, and a separate new-story setup pass."""
    pieces = command.split(None, 1)
    root = pieces[0] if pieces else ""
    if root in {
        "/status",
        "/trackers",
        "/help",
        "/history",
        "/usage",
        "/director",
        "/narrative",
        "/session",
        "/character",
        "/new",
        "/retry",
        "/prompt",
    }:
        return True
    # New-session name entry must remain usable from the completed session.
    for key in (f"session_name_input:{chat_id}", f"conversation_setup:{chat_id}:{actor_id}"):
        try:
            value = json.loads(load_meta_value(db, key, "") or "{}")
            if not isinstance(value, dict) or float(value.get("expires_at") or 0) <= time.time():
                continue
            if value.get("actor_id") not in (None, "", actor_id) or value.get("session_id") not in (
                None,
                "",
                session_id,
            ):
                continue
            if key.startswith("conversation_setup:") and value.get("stage") != "session_name":
                continue
            return True
        except (ValueError, TypeError, RecursionError):
            continue
    return False


def guard_story_reset(db: sqlite3.Connection, chat_id: str, session_id: str) -> None:
    guard_story_mutation(db, chat_id, session_id)
    ending = load_ending_row(db, chat_id, session_id)
    if ending is not None and ending["lifecycle"] == "finale":
        raise ClosedStoryError("This story has entered its finale. Start a new story instead of resetting it.")
