"""Resolve actual single or ensemble story readers at application composition."""

from __future__ import annotations

import sqlite3
from typing import Literal

from bridge.card_content import card_fields_from_file
from bridge.group_core import group_state
from bridge.memory_contracts import MemoryReadScope
from bridge.memory_scope_store import resolve_memory_scope
from bridge.settings import AppSettings


def resolve_session_memory_scope(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict[str, str],
    fields: dict[str, str],
    *,
    through_rowid: int | None = None,
    principals: tuple[str, ...] | None = None,
    consumer: Literal["character", "narrator"] = "character",
    historical: bool | None = None,
    app_settings: AppSettings,
) -> MemoryReadScope | None:
    if principals is None:
        state = group_state(db, chat_id, session["session_id"])
        if state.get("enabled") and state.get("mode") == "autonomous":
            readers = [fields.get("name", "")]
            for member in list(state.get("members") or [])[:16]:
                try:
                    name = card_fields_from_file(str(member), app_settings=app_settings).get("name", "")
                except (OSError, ValueError, KeyError):
                    name = ""
                # An unresolved ensemble reader cannot silently disappear from an intersection.
                readers.append(name or f"unresolved character: {member}")
            principals = tuple(readers)
    return resolve_memory_scope(
        db,
        chat_id,
        session,
        fields,
        through_rowid=through_rowid,
        principals=principals,
        consumer=consumer,
        historical=historical,
    )
