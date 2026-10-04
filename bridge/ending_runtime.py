"""Bounded runtime admission for committed endings, separate from schema startup."""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import time
from typing import Any

from bridge.background import chat_job_lock, submit_background
from bridge.delivery_port import DeliveryPort
from bridge.delivery_progress import delivery_complete
from bridge.ending_repository import recoverable_ending_rows
from bridge.ending_service import load_ending_state
from bridge.epilogue_service import EndingWorkResult, complete_epilogue
from bridge.narrative_checkpoints import create_pre_finale_checkpoint_and_enter
from bridge.narrative_context import narrative_clock_is_current
from bridge.narrative_reconciliation import ensure_narrative_state_current
from bridge.narrative_repository import load_narrative_clock
from bridge.narrative_settings import load_session_narrative_settings
from bridge.persona_service import PersonaService
from bridge.provider_port import ProviderPort
from bridge.session_repository import load_session_row
from bridge.settings import AppSettings
from bridge.sqlite_store import db_connect


def maybe_enter_finale(db: sqlite3.Connection, chat_id: str, session_id: str) -> bool:
    ending = load_ending_state(db, chat_id, session_id)
    settings = load_session_narrative_settings(db, chat_id, session_id)
    if ending.lifecycle != "finale_ready" or settings.require_finale_confirmation:
        return False
    if settings.ending_mode != "closed_story":
        return False
    clock = load_narrative_clock(db, chat_id, session_id)
    if clock is None or not narrative_clock_is_current(clock):
        return False
    identity = f"{chat_id}\0{session_id}\0{ending.finale_ready_revision}\0{ending.lifecycle_revision}"
    create_pre_finale_checkpoint_and_enter(
        db,
        chat_id,
        session_id,
        expected_story_revision=clock["state_revision"],
        expected_lifecycle_revision=ending.lifecycle_revision,
        operation_id="auto-finale-" + hashlib.sha256(identity.encode()).hexdigest(),
    )
    return True


def recover_ending_workflow(
    db: sqlite3.Connection,
    token: str,
    api_key: str,
    chat_id: str,
    session: dict[str, str],
    *,
    provider_port: ProviderPort,
    delivery_port: DeliveryPort,
    persona_service: PersonaService,
    app_settings: AppSettings,
    manual: bool = False,
) -> EndingWorkResult:
    if db.in_transaction:
        raise ValueError("Ending recovery cannot perform provider work inside a transaction")
    sid = session["session_id"]
    ending = load_ending_state(db, chat_id, sid)
    if ending.lifecycle == "finale" and ending.finale_committed_rowid is not None:
        clock = load_narrative_clock(db, chat_id, sid)
        if clock is not None:
            try:
                ensure_narrative_state_current(
                    db,
                    api_key,
                    chat_id,
                    session,
                    clock["latest_rowid"],
                    provider_port=provider_port,
                    app_settings=app_settings,
                )
            except (ValueError, RuntimeError):
                return EndingWorkResult("finale", "The finale is saved. Retry reconciliation before finishing it.")
        ending = load_ending_state(db, chat_id, sid)
    if ending.lifecycle == "finale_ready":
        maybe_enter_finale(db, chat_id, sid)
        ending = load_ending_state(db, chat_id, sid)
    if ending.lifecycle in {"resolution_committed", "epilogue_pending", "epilogue_committed", "closed"}:
        return complete_epilogue(
            db,
            token,
            api_key,
            chat_id,
            session,
            provider_port=provider_port,
            delivery_port=delivery_port,
            persona_service=persona_service,
            app_settings=app_settings,
            manual=manual,
        )
    return EndingWorkResult(ending.lifecycle, "Continue the story; the finale has not reached a committed resolution.")


def _needs_recovery(db: sqlite3.Connection, chat_id: str, session_id: str) -> bool:
    ending = load_ending_state(db, chat_id, session_id)
    if ending.lifecycle in {"resolution_committed", "epilogue_pending", "epilogue_committed"}:
        return not ending.last_error
    if ending.lifecycle == "finale":
        return ending.finale_committed_rowid is not None and not narrative_clock_is_current(
            load_narrative_clock(db, chat_id, session_id)
        )
    if ending.lifecycle == "closed":
        return any(
            rowid is not None and not delivery_complete(db, rowid)
            for rowid in (ending.resolution_rowid, ending.epilogue_committed_rowid)
        )
    return False


def _ending_worker(
    chat_id: str,
    session_id: str,
    provider_port: ProviderPort,
    delivery_port: DeliveryPort,
    persona_service: PersonaService,
    app_settings: AppSettings,
    admission: Any,
) -> None:
    db = None
    locked = False
    chat_lock = chat_job_lock(chat_id)
    try:
        # A story worker still owns its normal reply delivery. Resume on the next bounded scan.
        locked = chat_lock.acquire(blocking=False)
        if not locked:
            return
        db = db_connect(app_settings=app_settings)
        session = load_session_row(db, chat_id, session_id)
        if session and _needs_recovery(db, chat_id, session_id):
            recover_ending_workflow(
                db,
                app_settings.bot_token,
                app_settings.api_key,
                chat_id,
                session,
                provider_port=provider_port,
                delivery_port=delivery_port,
                persona_service=persona_service,
                app_settings=app_settings,
            )
    except Exception as exc:
        logging.warning("Ending recovery paused without rewinding committed story: %s", type(exc).__name__)
    finally:
        if db is not None:
            db.close()
        if locked:
            chat_lock.release()
        admission.release()


def queue_ending_recovery(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict[str, str],
    *,
    provider_port: ProviderPort,
    delivery_port: DeliveryPort,
    persona_service: PersonaService,
    app_settings: AppSettings,
) -> bool:
    sid = session["session_id"]
    if not _needs_recovery(db, chat_id, sid):
        return False
    identity = f"ending:{app_settings.db_file.resolve()}:{chat_id}:{sid}"
    admission = chat_job_lock(identity)
    if not admission.acquire(blocking=False):
        return False
    try:
        submitted = submit_background(
            "ending_recovery",
            _ending_worker,
            chat_id,
            sid,
            provider_port,
            delivery_port,
            persona_service,
            app_settings,
            admission,
        )
        if not submitted:
            admission.release()
        return bool(submitted)
    except Exception:
        admission.release()
        raise


def queue_startup_ending_recovery(
    db: sqlite3.Connection,
    *,
    provider_port: ProviderPort,
    delivery_port: DeliveryPort,
    persona_service: PersonaService,
    app_settings: AppSettings,
) -> int:
    """Called only after runtime composition; a bounded reader which never calls a model."""
    count = 0
    for chat_id, session_id in recoverable_ending_rows(db, time.time(), limit=32):
        session = load_session_row(db, chat_id, session_id)
        if session is not None and queue_ending_recovery(
            db,
            chat_id,
            session,
            provider_port=provider_port,
            delivery_port=delivery_port,
            persona_service=persona_service,
            app_settings=app_settings,
        ):
            count += 1
    return count
