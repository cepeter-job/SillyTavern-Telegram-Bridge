"""Actor-bound asynchronous operations over the bridge's bounded utility executor."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import time
from contextlib import closing
from typing import Any

from bridge import miniapp_job_repository as store
from bridge.background import submit_background
from bridge.diagnostic_events import event
from bridge.miniapp_auth import MiniAppIdentity
from bridge.miniapp_context import text
from bridge.miniapp_errors import MiniAppError
from bridge.miniapp_types import Handler
from bridge.sqlite_store import write_transaction


def recover_interrupted_jobs(services: Any) -> None:
    with closing(services.db_factory()) as db:
        with write_transaction(db):
            store.initialize(db)
        with write_transaction(db):
            store.recover(db, time.time())


def job_status(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    with closing(services.db_factory()) as db:
        with write_transaction(db):
            store.initialize(db)
        result = store.load(db, who.user_id, text(values, "job_id", 64))
        if result is None:
            raise MiniAppError("Operation not found.", status=404, code="not_found")
        return result


def job_by_operation(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    """Recover a lost submission response without admitting or replaying work."""
    key = text(values, "operation_id", 100)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", key):
        raise MiniAppError("Invalid operation identifier.")
    with closing(services.db_factory()) as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='miniapp_jobs'").fetchone()
        previous = store.by_key(db, who.user_id, key) if exists else None
        result = store.load(db, who.user_id, previous[0]) if previous else None
        if result is None:
            raise MiniAppError(
                "Operation not found. Review saved state before starting another.", status=404, code="not_found"
            )
        return result


def recent_jobs(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    with closing(services.db_factory()) as db:
        with write_transaction(db):
            store.initialize(db)
        return {"jobs": store.recent(db, who.user_id)}


def submit_job(services: Any, who: MiniAppIdentity, kind: str, values: dict, work: Handler) -> dict:
    key = text(values, "operation_id", 100)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", key):
        raise MiniAppError("Invalid operation identifier.")
    fingerprint = hashlib.sha256(json.dumps([kind, values], sort_keys=True, allow_nan=False).encode()).hexdigest()
    job_id = secrets.token_hex(16)
    with closing(services.db_factory()) as db:
        with write_transaction(db):
            store.initialize(db)
        with write_transaction(db):
            previous = store.by_key(db, who.user_id, key)
            if previous:
                if previous[1] != fingerprint:
                    raise MiniAppError("Operation ID was already used for a different request.", status=409)
                return store.load(db, who.user_id, previous[0]) or {}
            if store.active_count(db, who.user_id) >= 1 or store.active_count(db) >= 4:
                raise MiniAppError(
                    "An operation is already pending. Let it finish before submitting another.", status=429, code="busy"
                )
            store.prune(db, who.user_id, time.time())
            store.create(db, job_id, who.user_id, key, fingerprint, kind, time.time())

    def execute() -> None:
        try:
            with closing(services.db_factory()) as db, write_transaction(db):
                store.update(db, job_id, "running", time.time())
            event("miniapp.job_started", operation_id=job_id, kind=kind)
            result = json.dumps(work(services, who, values), ensure_ascii=False, allow_nan=False)
            if len(result) > 524288:
                raise MiniAppError("Operation response exceeded the display limit.")
            with closing(services.db_factory()) as db, write_transaction(db):
                store.update(db, job_id, "succeeded", time.time(), result)
            event("miniapp.job_finished", operation_id=job_id, kind=kind, status="succeeded")
        except Exception as exc:
            error = str(exc) if isinstance(exc, MiniAppError) else "Operation failed; check configuration and retry."
            logging.warning("Mini App operation %s failed (%s)", kind, type(exc).__name__)
            with closing(services.db_factory()) as db, write_transaction(db):
                store.update(db, job_id, "failed", time.time(), error=error)
            event(
                "miniapp.job_finished", operation_id=job_id, kind=kind, status="failed", error_type=type(exc).__name__
            )

    accepted = submit_background("miniapp", execute)
    event("miniapp.job_submitted", operation_id=job_id, kind=kind, accepted=accepted)
    if not accepted:
        with closing(services.db_factory()) as db, write_transaction(db):
            store.update(db, job_id, "failed", time.time(), error="Worker capacity is full. Try again later.")
    return job_status(services, who, {"job_id": job_id})
