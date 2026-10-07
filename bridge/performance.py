"""Optional low-overhead timing instrumentation for bridge hot paths."""

from __future__ import annotations

import logging
import re
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

from bridge.settings import AppSettings

_OPERATION: ContextVar[str | None] = ContextVar("performance_operation", default=None)


def performance_enabled(*, app_settings: AppSettings) -> bool:
    return app_settings.performance_log


def _log_duration(name: str, duration_ms: float, fields: dict[str, object]) -> None:
    # Scalars only: no provider text, prompt, credential, actor or session values.
    safe = " ".join(
        f"{key}={value}"
        for key, value in fields.items()
        if re.fullmatch(r"[A-Za-z0-9_]{1,40}", key) and type(value) in (bool, int, float)
    )
    operation = _OPERATION.get()
    suffix = (f" operation_id={operation}" if operation else "") + (f" {safe}" if safe else "")
    logging.info("perf span=%s duration_ms=%.3f%s", name, max(0, duration_ms), suffix)


@contextmanager
def operation_scope(operation_id: int | None = None, *, app_settings: AppSettings, queued_at: float | None = None):
    """Bind trusted durable job identity in this execution context, never callable arguments."""
    if not performance_enabled(app_settings=app_settings):
        yield
        return
    if operation_id is not None and (type(operation_id) is not int or operation_id <= 0):
        raise ValueError("Performance operation identity must be a positive durable job ID")
    identifier = f"job-{operation_id}" if operation_id is not None else (_OPERATION.get() or uuid.uuid4().hex)
    token = _OPERATION.set(identifier)
    try:
        if queued_at is not None:
            _log_duration("queue_wait", (time.time() - queued_at) * 1000, {})
        yield
    finally:
        _OPERATION.reset(token)


def observed_operation(function):
    """Keep direct use-case calls correlated; a durable worker's scope takes precedence."""

    @wraps(function)
    def observed(*args, **kwargs):
        app_settings = kwargs["app_settings"]
        if not performance_enabled(app_settings=app_settings):
            return function(*args, **kwargs)
        with operation_scope(app_settings=app_settings):
            return function(*args, **kwargs)

    return observed


@contextmanager
def perf_span(name: str, *, app_settings: AppSettings, **fields: object):
    if not performance_enabled(app_settings=app_settings):
        yield
        return
    started = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000
        _log_duration(name, elapsed_ms, fields)


def timed_call(name: str, function, *args, app_settings: AppSettings, **kwargs):
    with perf_span(name, app_settings=app_settings):
        return function(*args, **kwargs)
