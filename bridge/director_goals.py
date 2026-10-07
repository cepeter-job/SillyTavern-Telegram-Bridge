"""The shared session Director objective and its Group Director adapter."""

from __future__ import annotations

import json
import re
import sqlite3

from bridge.delivery_port import DeliveryPort
from bridge.director_goal_panel import director_goal_panel
from bridge.director_goal_repository import load_director_goal as _repo_load_director_goal
from bridge.director_guidance import active_director_plan
from bridge.director_repository import load_director_state
from bridge.director_service import DirectorService
from bridge.extension_registry import extension_registry_snapshot as _extension_registry_snapshot
from bridge.extension_registry import register_command_route as _register_command_route
from bridge.group_director_service import DirectorCustomization
from bridge.narrative_context import load_narrative_state, narrative_clock_is_current, narrative_context_for_session
from bridge.narrative_repository import load_narrative_clock
from bridge.sqlite_store import write_transaction
from bridge.topic_scope import parse_topic_scope

_DIRECTOR_GOAL_MAX_CHARS = 4000


def normalize_director_goal(value: str) -> str:
    if not isinstance(value, str) or len(value) > _DIRECTOR_GOAL_MAX_CHARS:
        raise ValueError("Director objectives may contain at most 4,000 characters.")
    return re.sub(r"\s+", " ", value).strip()


def get_director_goal(db: sqlite3.Connection, chat_id: str, session_id: str) -> str:
    return _repo_load_director_goal(db, chat_id, session_id)


def set_director_goal(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    goal: str,
) -> str:
    value = normalize_director_goal(goal)
    with write_transaction(db):
        clock = load_narrative_clock(db, chat_id, session_id)
        if clock is None:
            raise ValueError("This story no longer exists.")
        result = DirectorService().accept_manual_direction(
            db,
            chat_id,
            session_id,
            direction=value,
            scope="persistent",
            expected_revision=clock["state_revision"],
            expected_director_revision=load_director_state(db, chat_id, session_id)["state_revision"],
        )
        if result.result != "accepted":
            raise ValueError(result.reason)
    return value


def director_goal_policy(db: sqlite3.Connection, chat_id: str, session: dict[str, str]) -> DirectorCustomization:
    """Expose one canonical plan and objective; never start another planning call."""
    sid = session["session_id"]
    goal = get_director_goal(db, chat_id, sid)
    active = active_director_plan(db, chat_id, sid)
    plan = json.loads(active["active_proposal_json"]) if active else {}
    viewpoint = str(plan.get("viewpoint") or "")
    if not viewpoint and narrative_clock_is_current(load_narrative_clock(db, chat_id, sid)):
        viewpoint = load_narrative_state(db, chat_id, sid).viewpoint_character
    speaker_context = (
        (
            "Hidden scene objective: " + goal + " Advance it only when natural for the current speaker. "
            "Established continuity and believable character behavior take priority. "
            "Never mention, quote, or expose this objective."
        )
        if goal
        else ""
    )
    return DirectorCustomization(
        speaker=str(plan.get("speaker") or ""),
        viewpoint=viewpoint,
        direction=str(plan.get("direction") or plan.get("purpose") or ""),
        speaker_context=speaker_context,
        narrative_context=narrative_context_for_session(db, chat_id, sid, "group"),
    )


def send_director_goal_menu(
    token: str,
    chat_id: str,
    db: sqlite3.Connection,
    session: dict[str, str],
    message_id: int | None = None,
    *,
    delivery_port: DeliveryPort,
    request_context,
) -> None:
    goal = get_director_goal(db, chat_id, session["session_id"])
    text, markup = director_goal_panel(goal)
    method = "editMessageText" if message_id else "sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": markup,
    }
    if message_id:
        payload["message_id"] = message_id
    delivery_port.send_panel_request(
        token,
        method,
        payload,
        request_context=request_context,
    )


def handle_director_goal_command(
    db: sqlite3.Connection,
    token: str,
    chat_id: str,
    session: dict[str, str],
    command: str,
    *,
    delivery_port: DeliveryPort,
    request_context,
) -> None:
    """Keep Group Director objective aliases on the canonical review panel."""
    if parse_topic_scope(chat_id)[1] is None:
        delivery_port.send_text(token, chat_id, "Director goals are available only inside a Telegram Forum Topic.")
        return
    send_director_goal_menu(
        token,
        chat_id,
        db,
        session,
        delivery_port=delivery_port,
        request_context=request_context,
    )

def _director_goal_command_route(
    db,
    token,
    api_key,
    model,
    fields,
    chat_id,
    stripped,
    command,
    session,
    session_id,
    current_model,
    current_persona,
    user_name,
    operation_id=None,
    *,
    request_context,
    delivery_port,
    provider_port,
):
    if command == "/group goal" or command.startswith("/group goal "):
        handle_director_goal_command(
            db,
            token,
            chat_id,
            session,
            stripped,
            delivery_port=delivery_port,
            request_context=request_context,
        )
        return True
    return False


def register_director_goal_extensions() -> None:
    """Register Director Goal hooks once in the extension registry."""
    snapshot = _extension_registry_snapshot()
    if "director_goals" not in snapshot["command_routes"]:
        _register_command_route(
            "director_goals",
            _director_goal_command_route,
        )
