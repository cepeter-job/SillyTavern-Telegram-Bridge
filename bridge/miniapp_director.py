"""Authenticated Director Room adapters; planning rules stay in canonical owners."""

from __future__ import annotations

from functools import partial
from typing import Any

from bridge.alternate_ending import create_alternate_ending
from bridge.alternate_ending_memory import seed_alternate_ending_memory
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
from bridge.ending_controls import configure_ending, confirm_finale, require_ending_revision, saved_ending
from bridge.ending_runtime import maybe_enter_finale, recover_ending_workflow
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
        maybe_enter_finale(scope.db, scope.chat_id, scope.session["session_id"])
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


def save_ending_controls(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    revision = digest(values, "revision")
    with session_scope(services, who, values, write=True) as scope:
        configure_ending(
            scope.db,
            scope.chat_id,
            scope.session["session_id"],
            revision,
            mode=values.get("mode"),
            require_confirmation=values.get("require_confirmation"),
        )
        return {"saved": True}


def begin_finale(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    require_confirmation(values)
    revision = digest(values, "revision")
    operation = text(values, "operation_id", 100)
    with session_scope(services, who, values, write=True) as scope:
        checkpoint = confirm_finale(
            scope.db, scope.chat_id, scope.session["session_id"], revision, operation_id="finale-" + operation
        )
        return {
            "saved": True,
            "checkpoint_id": checkpoint.checkpoint_id,
            "message": "Checkpoint saved. Continue the story in Telegram to play its finale.",
        }


def recover_ending(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    require_confirmation(values)
    revision = digest(values, "revision")
    with session_scope(services, who, values, write=True) as scope:
        require_ending_revision(scope.db, scope.chat_id, scope.session["session_id"], revision)
        result = recover_ending_workflow(
            scope.db,
            services.config.bot_token,
            services.config.api_key,
            scope.chat_id,
            scope.session,
            provider_port=services.provider,
            delivery_port=services.delivery,
            persona_service=services.persona,
            app_settings=services.config,
            manual=True,
        )
        return {"completed": result.completed, "delivered": result.delivered, "message": result.message}


def get_ending(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    with session_scope(services, who, values) as scope:
        return saved_ending(scope.db, scope.chat_id, scope.session["session_id"])


def alternate_ending(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    require_confirmation(values)
    revision = digest(values, "revision")
    checkpoint = text(values, "checkpoint_id", 100)
    operation = text(values, "operation_id", 100)
    with session_scope(services, who, values, write=True) as scope:
        require_ending_revision(scope.db, scope.chat_id, scope.session["session_id"], revision)
        result = create_alternate_ending(
            scope.db,
            scope.chat_id,
            scope.session["session_id"],
            checkpoint,
            operation,
            seed_memory=partial(seed_alternate_ending_memory, app_settings=services.config),
        )
        return {
            "applied": result.applied,
            "session": result.session,
            "memory_status": result.memory_status,
            "message": "Alternate Ending is ready. The original completed story remains unchanged."
            if result.applied
            else "The saved alternate story is finishing memory setup.",
        }


def routes() -> list[ApiRoute]:
    return [
        ApiRoute("GET", "/director", get_director),
        ApiRoute("POST", "/director/alternate-ending", alternate_ending, "alternate_ending"),
        ApiRoute("GET", "/director/ending", get_ending),
        ApiRoute("PATCH", "/director/ending-controls", save_ending_controls),
        ApiRoute("POST", "/director/begin-finale", begin_finale),
        ApiRoute("POST", "/director/recover-ending", recover_ending, "ending_recovery"),
        ApiRoute("PATCH", "/director/ending-goal", edit_ending_goal),
        ApiRoute("PATCH", "/director/arc-guidance", edit_arc_guidance),
        ApiRoute("PATCH", "/director/direction", edit_direction),
        ApiRoute("POST", "/director/reassess", reassess, "director_reassess"),
        ApiRoute("POST", "/director/thread", choose_thread),
        ApiRoute("PATCH", "/director/controls", save_controls),
    ]
