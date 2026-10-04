"""Bounded post-commit orchestration of facts first, then optional planning."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict
from functools import partial
from typing import Any

from bridge.background import chat_job_lock, submit_background
from bridge.director_cadence import director_due, director_event_key
from bridge.director_guidance import active_director_plan
from bridge.director_repository import (
    director_ending_lifecycle,
    director_story_turns,
    load_director_state,
    observe_director_event_key,
)
from bridge.director_service import DirectorService
from bridge.ending_service import load_ending_state
from bridge.extension_context import PostRetainContext
from bridge.extension_registry import register_post_retain_hook
from bridge.narrative_arc_repository import list_arc_rows
from bridge.narrative_context import load_narrative_state, narrative_clock_is_current
from bridge.narrative_reconciliation import queue_narrative_reconciliation
from bridge.narrative_repository import list_narrative_threads, load_narrative_clock
from bridge.narrative_settings import load_session_narrative_settings
from bridge.persona_service import PersonaService
from bridge.provider_port import ProviderPort
from bridge.session_repository import load_session_row
from bridge.settings import AppSettings
from bridge.sqlite_store import db_connect, write_transaction

_ENDING = frozenset({"resolution_committed", "epilogue_pending", "epilogue_committed", "closed"})


def _due(db: sqlite3.Connection, chat_id: str, session_id: str, event: str) -> bool:
    clock = load_narrative_clock(db, chat_id, session_id)
    if (
        not narrative_clock_is_current(clock)
        or clock is None
        or director_ending_lifecycle(db, chat_id, session_id) in _ENDING
    ):
        return False
    state = load_narrative_state(db, chat_id, session_id)
    if not state.active_scene_id:
        return False
    threads = list_narrative_threads(db, chat_id, session_id, limit=64)
    current = load_director_state(db, chat_id, session_id)
    key = director_event_key(
        state,
        clock,
        threads,
        arcs=list_arc_rows(db, chat_id, session_id, limit=128),
        ending=asdict(load_ending_state(db, chat_id, session_id)),
        manual_objective=current["goal"],
        arc_guidance=current.get("arc_guidance_json", "{}"),
    )
    if not current["last_event_key"]:
        with write_transaction(db):
            if load_narrative_clock(db, chat_id, session_id) != clock:
                return False
            observe_director_event_key(db, chat_id, session_id, key)
        current = load_director_state(db, chat_id, session_id)
    if (
        current["direction_source"] == "user"
        and current["active_direction"]
        and active_director_plan(db, chat_id, session_id) is None
    ):
        # A manual plan which no longer governs Story cannot pause the Director.
        current = current | {"active_direction": ""}
        event = event or "stale"
    return director_due(
        current,
        state,
        settings=load_session_narrative_settings(db, chat_id, session_id),
        completed_turns=director_story_turns(db, chat_id, session_id),
        now=time.time(),
        event=event,
        event_key=key,
    )


def _director_worker(
    chat_id: str,
    session_id: str,
    provider_port: ProviderPort,
    app_settings: AppSettings,
    persona_service: PersonaService | None,
    admission: Any,
    event: str,
) -> None:
    try:
        db = db_connect(app_settings=app_settings)
        try:
            session = load_session_row(db, chat_id, session_id)
            if session is not None and _due(db, chat_id, session_id, event):
                DirectorService().reassess(
                    db,
                    "",
                    chat_id,
                    session,
                    provider_port=provider_port,
                    app_settings=app_settings,
                    persona_service=persona_service,
                    reason=event or "adaptive",
                )
        finally:
            db.close()
    finally:
        admission.release()


def queue_director_reassessment(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict[str, str],
    *,
    provider_port: ProviderPort,
    app_settings: AppSettings,
    persona_service: PersonaService | None = None,
    event: str = "",
) -> bool:
    if db.in_transaction or not _due(db, chat_id, session["session_id"], event):
        return False
    key = json.dumps(["director-reassess", str(app_settings.db_file.resolve()), chat_id, session["session_id"]])
    admission = chat_job_lock(key)
    if not admission.acquire(blocking=False):
        return False
    try:
        accepted = submit_background(
            "director_reassess",
            _director_worker,
            chat_id,
            session["session_id"],
            provider_port,
            app_settings,
            persona_service,
            admission,
            event,
        )
    except BaseException:
        admission.release()
        raise
    if not accepted:
        admission.release()
    return accepted


def _post_retain(context: PostRetainContext) -> None:
    # Capture ports per admitted call, not in a process-wide app/service registry.
    queue_narrative_reconciliation(
        context.db,
        context.chat_id,
        context.session,
        provider_port=context.provider_port,
        app_settings=context.app_settings,
        on_current=partial(
            queue_director_reassessment,
            provider_port=context.provider_port,
            app_settings=context.app_settings,
            persona_service=context.persona_service,
        ),
    )


def register_narrative_extensions() -> None:
    register_post_retain_hook("narrative", _post_retain)
