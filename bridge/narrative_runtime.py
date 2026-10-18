"""Bounded background admission and scheduling for committed narrative reconciliation."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any

from bridge.background import chat_job_lock, submit_background
from bridge.narrative_context import narrative_clock_is_current
from bridge.narrative_reconciliation import reconcile_narrative_state_now
from bridge.narrative_repository import load_narrative_clock
from bridge.provider_port import ProviderPort
from bridge.session_repository import load_session_row
from bridge.settings import AppSettings
from bridge.sqlite_store import db_connect


def _reconciliation_worker(
    chat_id: str,
    session_id: str,
    provider_port: ProviderPort,
    app_settings: AppSettings,
    admission: Any,
    on_current: Callable[[sqlite3.Connection, str, dict[str, str]], object] | None = None,
) -> None:
    worker_db = None
    continue_work = False
    session = None
    try:
        worker_db = db_connect(app_settings=app_settings)
        session = load_session_row(worker_db, chat_id, session_id)
        before = load_narrative_clock(worker_db, chat_id, session_id)
        if session and before:
            result = reconcile_narrative_state_now(
                worker_db, "", chat_id, session, provider_port=provider_port, app_settings=app_settings
            )
            after = load_narrative_clock(worker_db, chat_id, session_id)
            continue_work = bool(
                after
                and not narrative_clock_is_current(after)
                and (
                    result.updated_through_rowid > before["updated_through_rowid"]
                    or after["history_revision"] != before["history_revision"]
                )
            )
    finally:
        admission.release()
        try:
            if continue_work and worker_db is not None and session is not None:
                queue_narrative_reconciliation(
                    worker_db,
                    chat_id,
                    session,
                    provider_port=provider_port,
                    app_settings=app_settings,
                    on_current=on_current,
                )
            elif worker_db is not None and session is not None and on_current is not None:
                if narrative_clock_is_current(load_narrative_clock(worker_db, chat_id, session_id)):
                    on_current(worker_db, chat_id, session)
        finally:
            if worker_db is not None:
                worker_db.close()


def queue_narrative_reconciliation(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict[str, str],
    *,
    provider_port: ProviderPort,
    app_settings: AppSettings,
    on_current: Callable[[sqlite3.Connection, str, dict[str, str]], object] | None = None,
) -> bool:
    if db.in_transaction:
        return False
    session_id = session["session_id"]
    clock = load_narrative_clock(db, chat_id, session_id)
    if clock is None:
        return False
    if narrative_clock_is_current(clock):
        if on_current is not None:
            on_current(db, chat_id, session)
        return False
    key = json.dumps(["narrative-reconciliation", str(app_settings.db_file.resolve()), chat_id, session_id])
    admission = chat_job_lock(key)
    if not admission.acquire(blocking=False):
        return False
    try:
        accepted = submit_background(
            "narrative_reconcile",
            _reconciliation_worker,
            chat_id,
            session_id,
            provider_port,
            app_settings,
            admission,
            on_current,
        )
    except BaseException:
        admission.release()
        raise
    if not accepted:
        admission.release()
    return accepted
