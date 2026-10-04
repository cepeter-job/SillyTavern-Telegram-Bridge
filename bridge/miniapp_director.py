"""Authenticated Director Room adapters; planning rules stay in canonical owners."""

from __future__ import annotations

from typing import Any

from bridge.director_room import (
    apply_arc_guidance,
    apply_controls,
    apply_direction,
    apply_ending_goal,
    director_room,
    require_room_revision,
    steer_thread,
)
from bridge.director_service import DirectorService
from bridge.miniapp_auth import MiniAppIdentity
from bridge.miniapp_context import digest, require_confirmation, session_scope, text
from bridge.miniapp_types import ApiRoute
from bridge.model_selection import director_reasoning_for_session, task_model_for_session
from bridge.narrative_settings import load_session_narrative_settings


def get_director(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    with session_scope(services, who, values) as scope:
        sid = scope.session["session_id"]
        return director_room(scope.db, scope.chat_id, sid) | {
            "session": scope.session,
            "settings": load_session_narrative_settings(scope.db, scope.chat_id, sid).to_dict(),
            "director_model": task_model_for_session(
                scope.db, scope.chat_id, scope.session, "director", app_settings=services.config
            ),
            "director_reasoning": director_reasoning_for_session(scope.db, scope.chat_id, sid),
        }


def edit_direction(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    revision = digest(values, "revision")
    direction = text(values, "direction", 4000, required=False)
    mode = text(values, "scope", 24)
    with session_scope(services, who, values, write=True) as scope:
        result = apply_direction(scope.db, scope.chat_id, scope.session["session_id"], revision, direction, mode)
        return {"saved": result.result == "accepted", "message": result.reason}


def reassess(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    require_confirmation(values)
    revision = digest(values, "revision")
    with session_scope(services, who, values, write=True) as scope:
        require_room_revision(scope.db, scope.chat_id, scope.session["session_id"], revision)
        result = DirectorService().reassess(
            scope.db,
            services.config.api_key,
            scope.chat_id,
            scope.session,
            reason="manual",
            provider_port=services.provider,
            persona_service=services.persona,
            app_settings=services.config,
        )
        return {"accepted": result.result == "accepted", "message": result.reason}


def choose_thread(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    revision = digest(values, "revision")
    identifier = text(values, "thread_id", 100)
    with session_scope(services, who, values, write=True) as scope:
        result = steer_thread(
            scope.db,
            scope.chat_id,
            scope.session,
            revision,
            identifier,
            persona_service=services.persona,
            app_settings=services.config,
        )
        return {"saved": result.result == "accepted", "message": result.reason}


def save_controls(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    revision = digest(values, "revision")
    with session_scope(services, who, values, write=True) as scope:
        apply_controls(
            scope.db,
            scope.chat_id,
            scope.session["session_id"],
            revision,
            cadence=values.get("cadence"),
            interval=values.get("interval"),
            reasoning=values.get("reasoning"),
        )
        return {"saved": True}


def edit_ending_goal(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    revision = digest(values, "revision")
    goal = text(values, "goal", 4000, required=False)
    with session_scope(services, who, values, write=True) as scope:
        apply_ending_goal(scope.db, scope.chat_id, scope.session["session_id"], revision, goal)
        return {"saved": True}


def edit_arc_guidance(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    revision = digest(values, "revision")
    identifier = text(values, "arc_id", 100)
    direction = text(values, "direction", 1000, required=False)
    with session_scope(services, who, values, write=True) as scope:
        result = apply_arc_guidance(
            scope.db, scope.chat_id, scope.session["session_id"], revision, identifier, direction
        )
        return {"saved": result.result == "accepted", "message": result.reason}


def routes() -> list[ApiRoute]:
    return [
        ApiRoute("GET", "/director", get_director),
        ApiRoute("PATCH", "/director/ending-goal", edit_ending_goal),
        ApiRoute("PATCH", "/director/arc-guidance", edit_arc_guidance),
        ApiRoute("PATCH", "/director/direction", edit_direction),
        ApiRoute("POST", "/director/reassess", reassess, "director_reassess"),
        ApiRoute("POST", "/director/thread", choose_thread),
        ApiRoute("PATCH", "/director/controls", save_controls),
    ]
