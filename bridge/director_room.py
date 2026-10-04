"""Read-only Director Room views and revision-bound manual planning actions."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Any

from bridge.alternate_ending_repository import linked_alternate_checkpoint
from bridge.delivery_progress import delivery_complete
from bridge.director_repository import (
    append_director_decision,
    director_decision_history,
    load_director_state,
    store_arc_guidance,
)
from bridge.director_service import DirectorDecision, DirectorService
from bridge.ending_service import ending_goal_history, load_ending_state, set_ending_goal
from bridge.ending_values import EndingState
from bridge.model_selection import director_reasoning_for_session, set_director_reasoning
from bridge.narrative_arc_repository import list_arc_rows, load_arc_row
from bridge.narrative_repository import list_narrative_threads, load_narrative_clock, load_narrative_state_row
from bridge.narrative_settings import (
    load_session_narrative_settings,
    normalize_narrative_settings,
    save_session_narrative_settings,
)
from bridge.persona_service import PersonaService
from bridge.settings import AppSettings
from bridge.sqlite_store import write_transaction


def director_room(db: sqlite3.Connection, chat_id: str, session_id: str) -> dict[str, Any]:
    """Return a bounded, safe view, never provider prompts, raw proposals or leases."""
    clock = load_narrative_clock(db, chat_id, session_id)
    if clock is None:
        raise ValueError("This story no longer exists. Open /director for the current story.")
    director = load_director_state(db, chat_id, session_id)
    state = load_narrative_state_row(db, chat_id, session_id) or {}
    ending = load_ending_state(db, chat_id, session_id)
    settings = load_session_narrative_settings(db, chat_id, session_id)
    notes = json.loads(director.get("arc_guidance_json", "{}"))
    identity = (
        chat_id,
        session_id,
        clock,
        director["state_revision"],
        (ending.lifecycle, ending.lifecycle_revision, ending.goal_revision),
        director_reasoning_for_session(db, chat_id, session_id),
    )
    revision = hashlib.sha256(json.dumps(identity, sort_keys=True, default=str).encode()).hexdigest()
    history = [
        {
            "id": row["decision_id"],
            "source": row["source"],
            "result": row["result"],
            "direction": str(row.get("accepted_direction", ""))[:500],
            "reason": str(row["reason"])[:240],
        }
        for row in director_decision_history(db, chat_id, session_id, limit=10)
    ]
    return {
        "session_id": session_id,
        "revision": revision,
        "mutable": ending.lifecycle not in {"resolution_committed", "epilogue_pending", "epilogue_committed", "closed"},
        "lifecycle": ending.lifecycle,
        "phase": str(state.get("story_phase", "setup")),
        "scene": {
            "scene_id": str(state.get("active_scene_id", "")),
            "thread_id": str(state.get("active_thread_id", "")),
            "viewpoint": str(state.get("viewpoint_character", "")),
            "pov": str(state.get("pov_mode", "")),
        },
        "direction": str(director["active_direction"])[:4000],
        "scope": str(director.get("direction_scope", "")),
        "objective": str(director["goal"])[:4000],
        "degraded": bool(director["degraded_state"]),
        "threads": [
            {"thread_id": row["thread_id"], "title": row["title"], "status": row["status"]}
            for row in list_narrative_threads(db, chat_id, session_id)[:16]
        ],
        "arcs": [
            {
                "arc_id": row["arc_id"],
                "title": row["title"],
                "status": row["status"],
                "phase": row["phase"],
                "importance": row["importance"],
                "summary": row["summary"][:500],
                "open_questions": row["open_questions"],
                "guidance": notes.get(row["arc_id"], ""),
            }
            for row in list_arc_rows(db, chat_id, session_id, limit=32)
        ],
        "ending": {
            "mode": settings.ending_mode,
            "lifecycle": ending.lifecycle,
            "goal": ending.current_goal,
            "editable": ending.lifecycle in {"open", "finale_ready"},
            "require_confirmation": settings.require_finale_confirmation,
            "reason": ending.readiness_reason,
            "alternate_available": linked_alternate_checkpoint(db, chat_id, session_id) is not None,
            "checkpoint_id": ending.checkpoint_id or "",
            "has_resolution": ending.resolution_rowid is not None,
            "has_epilogue": ending.epilogue_committed_rowid is not None,
            "delivery_pending": any(
                rowid is not None and not delivery_complete(db, rowid)
                for rowid in (ending.resolution_rowid, ending.epilogue_committed_rowid)
            ),
            "recovery_needed": ending.lifecycle in {"resolution_committed", "epilogue_pending", "epilogue_committed"},
            "error": bool(ending.last_error),
            "history": [
                {
                    "revision": row.goal_revision,
                    "source": row.source,
                    "reason": row.reason[:300],
                    "previous": row.previous_goal[:500],
                    "goal": row.new_goal[:500],
                }
                for row in ending_goal_history(db, chat_id, session_id, limit=10)
            ],
        },
        "history": history,
        "cadence": load_session_narrative_settings(db, chat_id, session_id).director_cadence_mode,
    }


def require_room_revision(db: sqlite3.Connection, chat_id: str, session_id: str, revision: str) -> dict[str, Any]:
    view = director_room(db, chat_id, session_id)
    if view["revision"] != revision:
        raise ValueError("The Director Room changed. Reopen /director before editing it.")
    if not view["mutable"]:
        raise ValueError("This story has ended. Its Director Room is read-only.")
    return view


def apply_direction(
    db: sqlite3.Connection, chat_id: str, session_id: str, revision: str, direction: str, scope: str
) -> DirectorDecision:
    """Commit a user-authored plan, never story facts, against the displayed state."""
    with write_transaction(db):
        require_room_revision(db, chat_id, session_id, revision)
        clock = load_narrative_clock(db, chat_id, session_id)
        if clock is None:
            raise ValueError("This story no longer exists.")
        director = load_director_state(db, chat_id, session_id)
        result = DirectorService().accept_manual_direction(
            db,
            chat_id,
            session_id,
            direction=direction,
            scope=scope,
            expected_revision=int(clock["state_revision"]),
            expected_director_revision=int(director["state_revision"]),
        )
        if result.result != "accepted":
            raise ValueError(result.reason)
        return result


def steer_thread(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict[str, str],
    revision: str,
    thread_id: str,
    *,
    persona_service: PersonaService,
    app_settings: AppSettings,
) -> DirectorDecision:
    """Select a known thread using its last committed viewpoint, as a manual plan."""
    from bridge.director_contracts import DirectorProposal
    from bridge.director_prompt import build_director_input
    from bridge.narrative_context import load_narrative_state
    from bridge.narrative_repository import load_narrative_scene, load_narrative_thread

    session_id = session["session_id"]
    with write_transaction(db):
        require_room_revision(db, chat_id, session_id, revision)
        target = load_narrative_thread(db, chat_id, session_id, thread_id)
        if not target or target["status"] == "resolved":
            raise ValueError("That thread is unavailable. Refresh Director Room.")
        previous_scene = load_narrative_scene(db, chat_id, session_id, target["last_scene_id"])
        if not previous_scene:
            raise ValueError("This thread has no established scene to return to.")
        state = load_narrative_state(db, chat_id, session_id)
        settings = load_session_narrative_settings(db, chat_id, session_id)
        director = load_director_state(db, chat_id, session_id)
        _, cast, users, threads = build_director_input(
            db, chat_id, session, state, settings, director, app_settings=app_settings, persona_service=persona_service
        )
        viewpoint = str(previous_scene["viewpoint_character"])
        if viewpoint:
            cast.add(viewpoint)
        proposal = DirectorProposal(
            1,
            "transition_scene",
            state.state_revision,
            direction=f"Follow the {target['title']} thread next.",
            scene_id=state.active_scene_id,
            thread_id=thread_id,
            viewpoint=viewpoint,
            pov=settings.pov_mode,
            user_present=None,
            purpose="Resume an established storyline without inventing user decisions.",
            transition_type="thread_switch",
        )
        return DirectorService().accept_manual_transition(
            db,
            chat_id,
            session_id,
            proposal,
            expected_director_revision=int(director["state_revision"]),
            valid_characters=cast,
            user_characters=users,
            valid_threads=threads | {thread_id},
        )


def apply_controls(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    revision: str,
    *,
    cadence: Any,
    interval: Any,
    reasoning: Any,
) -> None:
    """Apply cadence and independent reasoning atomically with stale-panel protection."""
    if (
        not isinstance(cadence, str)
        or cadence not in {"adaptive", "fixed"}
        or type(interval) is not int
        or not 1 <= interval <= 100
    ):
        raise ValueError("Choose Adaptive or a whole-number interval from 1 to 100 turns.")
    if type(reasoning) is not int or not 0 <= reasoning <= 32000:
        raise ValueError("Director reasoning must be a whole number from 0 to 32000.")
    with write_transaction(db):
        require_room_revision(db, chat_id, session_id, revision)
        current = load_session_narrative_settings(db, chat_id, session_id)
        revised = normalize_narrative_settings(
            current.to_dict()
            | {
                "preset": "custom",
                "director_cadence_mode": cadence,
                "director_fixed_interval": interval,
            }
        )
        save_session_narrative_settings(db, chat_id, session_id, revised)
        set_director_reasoning(db, chat_id, session_id, reasoning)


def apply_ending_goal(db: sqlite3.Connection, chat_id: str, session_id: str, revision: str, goal: str) -> EndingState:
    with write_transaction(db):
        view = require_room_revision(db, chat_id, session_id, revision)
        if not view["ending"]["editable"]:
            raise ValueError("The ending goal is locked once the finale begins.")
        clock = load_narrative_clock(db, chat_id, session_id)
        ending = load_ending_state(db, chat_id, session_id)
        if clock is None:
            raise ValueError("This story no longer exists.")
        return set_ending_goal(
            db,
            chat_id,
            session_id,
            goal,
            source="user",
            story_revision=clock["state_revision"],
            expected_lifecycle_revision=ending.lifecycle_revision,
            expected_goal_revision=ending.goal_revision,
        )


def apply_arc_guidance(
    db: sqlite3.Connection, chat_id: str, session_id: str, revision: str, arc_id: str, direction: str
) -> DirectorDecision:
    if not isinstance(direction, str) or len(direction) > 1000 or not isinstance(arc_id, str) or len(arc_id) > 100:
        raise ValueError("Use an established arc and at most 1,000 characters of guidance.")
    with write_transaction(db):
        require_room_revision(db, chat_id, session_id, revision)
        if load_arc_row(db, chat_id, session_id, arc_id) is None:
            raise ValueError("This arc is no longer part of the current story. Refresh Director Room.")
        director = load_director_state(db, chat_id, session_id)
        clock = load_narrative_clock(db, chat_id, session_id)
        if clock is None:
            raise ValueError("This story no longer exists.")
        notes = json.loads(director.get("arc_guidance_json", "{}"))
        direction = direction.strip()
        if direction:
            notes[arc_id] = direction
        else:
            notes.pop(arc_id, None)
        encoded = json.dumps(notes, ensure_ascii=False, separators=(",", ":"))
        if len(notes) > 32 or len(encoded) > 32768:
            raise ValueError("Keep at most 32 concise arc directions; clear older notes before adding more.")
        now = time.time()
        if not store_arc_guidance(
            db, chat_id, session_id, encoded, expected_revision=director["state_revision"], now=now
        ):
            raise ValueError("The Director Room changed. Refresh before editing arc guidance.")
        identifier = append_director_decision(
            db,
            chat_id,
            session_id,
            source="user",
            result="accepted",
            expected_revision=clock["state_revision"],
            proposal_json=json.dumps({"arc_id": arc_id, "guidance": direction}),
            accepted_direction=direction,
            reason="Persistent arc guidance" if direction else "Arc guidance cleared",
            created_at=now,
        )
        return DirectorDecision(identifier, clock["state_revision"], "user", "accepted", "Arc guidance saved.")
