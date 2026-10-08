"""Preserve only diagnostic identity across queues and durable recovery."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Mapping
from contextvars import Context
from typing import ParamSpec, TypeVar

from bridge.diagnostic_events import bind_diagnostics, clean_fields, diagnostic_context, diagnostic_scope, event

P = ParamSpec("P")
T = TypeVar("T")
_ATTRIBUTE = "_bridge_diagnostic_identity"


def worker_identity(function: Callable[..., object]) -> dict[str, object]:
    captured = getattr(function, _ATTRIBUTE, None)
    return clean_fields(captured) if isinstance(captured, dict) else diagnostic_context()


def bind_worker(function: Callable[P, T], *, identity: Mapping[str, object] | None = None) -> Callable[P, T]:
    captured = worker_identity(function) if identity is None else clean_fields(identity)

    def capture() -> Callable[P, T]:
        with diagnostic_scope(**captured):
            return bind_diagnostics(function)

    # Start empty rather than copying arbitrary ContextVars or a dispatcher's
    # active session. Only allowlisted scalar identity survives admission.
    bound = Context().run(capture)
    setattr(bound, _ATTRIBUTE, captured)
    return bound


def durable_job_identity(db: sqlite3.Connection, job_id: int) -> dict[str, object]:
    identity: dict[str, object] = {"job_id": int(job_id), "request_id": f"job-{int(job_id)}"}
    try:
        row = db.execute(
            "SELECT update_id,chat_id,session_id,kind,attempts FROM jobs WHERE job_id=?", (int(job_id),)
        ).fetchone()
        if row is not None:
            update_id = int(row[0] or 0)
            attempts = int(row[4] or 0)
            identity.update(
                request_id=f"tg-{update_id}" if update_id > 0 else f"job-{int(job_id)}",
                chat_id=str(row[1]),
                session_id=str(row[2]),
                kind=str(row[3]),
                attempt=max(0, attempts) + 1,
                recovered=attempts > 0,
            )
    except (sqlite3.Error, TypeError, ValueError):
        event("job.identity_unavailable", level=logging.WARNING, job_id=int(job_id))
    return clean_fields(identity)
