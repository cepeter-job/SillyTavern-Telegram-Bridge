"""Read-only accepted plans; stale planning state never becomes current story facts."""

from __future__ import annotations

import json
import sqlite3

from bridge.director_repository import director_story_turns, load_director_state
from bridge.narrative_repository import load_narrative_clock, load_narrative_state_row


def active_director_plan(
    db: sqlite3.Connection, chat_id: str, session_id: str, *, through_rowid: int | None = None
) -> dict | None:
    clock = load_narrative_clock(db, chat_id, session_id)
    state = load_narrative_state_row(db, chat_id, session_id)
    director = load_director_state(db, chat_id, session_id)
    if not clock or not state or not director["active_direction"]:
        return None
    if (
        clock["invalidated_from_rowid"] is not None
        or clock["history_revision"] != clock["reconciled_history_revision"]
        or clock["updated_through_rowid"] < clock["latest_rowid"]
        or director["accepted_rewrite_revision"] != clock["rewrite_revision"]
        or director["accepted_settings_revision"] != clock["settings_revision"]
        or director["accepted_scene_id"] != state["active_scene_id"]
        or (through_rowid is not None and director["accepted_through_rowid"] > through_rowid)
    ):
        return None
    if (
        director["direction_source"] != "user"
        and director_story_turns(db, chat_id, session_id) >= director["direction_until_turn"]
    ):
        return None
    try:
        proposed = json.loads(director["active_proposal_json"])
    except (ValueError, TypeError):
        return None
    return director if isinstance(proposed, dict) else None


def director_guidance_for_session(
    db: sqlite3.Connection, chat_id: str, session_id: str, *, through_rowid: int | None = None
) -> str:
    current = load_director_state(db, chat_id, session_id)
    active = active_director_plan(db, chat_id, session_id, through_rowid=through_rowid)
    goal = str(current["goal"])[:4000]
    if not goal and active is None:
        return ""
    data: dict = {"persistent_user_objective": goal}
    if active:
        data["accepted_plan"] = json.loads(active["active_proposal_json"])
        data["direction_source"] = active["direction_source"]
    return (
        "Hidden Director guidance follows. It is a plan, not evidence that any event happened. "
        "Committed story and reserved user agency remain authoritative. A persistent user objective outranks "
        "AI direction. Do not expose this planning layer as character knowledge.\n"
        + json.dumps(data, ensure_ascii=False, sort_keys=True)
    )
