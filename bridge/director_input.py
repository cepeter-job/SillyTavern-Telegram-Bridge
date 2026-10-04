"""Actor-scoped, revision-bound input for explicit Director Room edits."""

from __future__ import annotations

import json
import sqlite3
import time

from bridge.director_room import apply_direction, require_room_revision
from bridge.metadata import get_meta, set_meta
from bridge.request_types import RequestContext
from bridge.sqlite_store import write_transaction
from bridge.telegram import send_text


def _key(chat_id: str, actor_id: str) -> str:
    return f"director_input:{chat_id}:{actor_id}"


def begin_director_input(
    db: sqlite3.Connection, chat_id: str, session_id: str, actor_id: str, revision: str, scope: str
) -> None:
    if not actor_id or scope not in {"next_scene", "persistent"}:
        raise ValueError("Choose a valid Director edit scope.")
    with write_transaction(db):
        require_room_revision(db, chat_id, session_id, revision)
        set_meta(
            db,
            _key(chat_id, actor_id),
            json.dumps(
                {
                    "session_id": session_id,
                    "actor_id": actor_id,
                    "revision": revision,
                    "scope": scope,
                    "expires_at": time.time() + 300,
                }
            ),
        )


def handle_director_input(
    db: sqlite3.Connection, token: str, chat_id: str, session: dict, text: str, request_context: RequestContext
) -> bool:
    key = _key(chat_id, request_context.actor_id)
    raw = get_meta(db, key, "")
    if not raw or (text.startswith("/") and text.strip().casefold() != "/cancel"):
        return False
    try:
        values = json.loads(raw)
        if not isinstance(values, dict):
            raise ValueError("Director input expired. Reopen /director.")
        if text.strip().casefold() == "/cancel":
            notice = "Director edit cancelled."
        elif values["session_id"] != session["session_id"] or float(values["expires_at"]) < time.time():
            notice = "This Director edit expired or belongs to another story. Reopen /director."
        else:
            apply_direction(db, chat_id, session["session_id"], values["revision"], text, values["scope"])
            notice = (
                "Director direction saved for the next scene."
                if values["scope"] == "next_scene"
                else ("Persistent Director objective saved. It remains in effect until you change or clear it.")
            )
    except (ValueError, KeyError, TypeError):
        notice = "The Director Room changed or the edit was invalid. Reopen /director and try again."
    with write_transaction(db):
        set_meta(db, key, "")
    send_text(token, chat_id, notice)
    return True
