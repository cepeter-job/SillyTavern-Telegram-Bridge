"""Tracker plot metadata refers to the existing Narrative owner without writing it."""

import sqlite3
from typing import Any

from bridge.narrative_arc_repository import load_arc_row
from bridge.narrative_context import narrative_clock_is_current
from bridge.narrative_repository import load_narrative_clock, load_narrative_thread

_LINKS = {"arc_id": load_arc_row, "thread_id": load_narrative_thread}


def validate_narrative_links(
    db: sqlite3.Connection, chat_id: str, session_id: str, payload: dict[str, Any], source_rowid: int
) -> None:
    for group in ("quests", "foreshadowing"):
        for item in payload.get(group, []):
            for field, loader in _LINKS.items():
                if not item.get(field):
                    continue
                row = loader(db, chat_id, session_id, item[field])
                if row is None or row["source_revision"] > source_rowid:
                    raise ValueError("Narrative link must name an established plot in this session")


def canonical_plot_context(
    db: sqlite3.Connection, chat_id: str, session_id: str, value: dict[str, Any], cutoff: int
) -> dict[str, Any]:
    links = [(field, identifier) for field in _LINKS if (identifier := value.get(field))]
    if not links:
        return value
    result = dict(value, status="native=unavailable")
    clock = load_narrative_clock(db, chat_id, session_id)
    if clock is None or not narrative_clock_is_current(clock, cutoff) or clock["updated_through_rowid"] > cutoff:
        return result
    plots = [_LINKS[field](db, chat_id, session_id, identifier) for field, identifier in links]
    if any(row is None or row["source_revision"] > cutoff for row in plots):
        return result
    result["status"] = "native=" + "/".join(dict.fromkeys(row["status"] for row in plots if row is not None))
    return result
