"""Explicit database/scheduler safety collaborators for durable jobs."""

from __future__ import annotations

import functools
import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Generic, TypeVar

from bridge.diagnostic_events import clean_fields, diagnostic_scope, event
from bridge.diagnostic_workers import bind_worker
from bridge.performance import operation_scope, perf_span, performance_enabled
from bridge.settings import AppSettings

T = TypeVar("T")


class DatabaseConnectionGate(Generic[T]):
    """Initialize each database path once, then use lightweight handles."""

    def __init__(
        self,
        initialize: Callable[[Path], T],
        open_lightweight: Callable[[Path], T],
    ) -> None:
        self._initialize = initialize
        self._open_lightweight = open_lightweight
        self._ready_paths: set[Path] = set()
        self._lock = threading.Lock()

    @staticmethod
    def _normalize(database_path: Path) -> Path:
        return Path(database_path).expanduser().resolve()

    def connect(self, database_path: Path) -> T:
        path = self._normalize(database_path)
        if path in self._ready_paths:
            return self._open_lightweight(path)

        with self._lock:
            if path in self._ready_paths:
                return self._open_lightweight(path)
            connection = self._initialize(path)
            self._ready_paths.add(path)
            return connection


def _job_identity(db: sqlite3.Connection, job_id: int) -> dict[str, object]:
    """Read existing durable identity; neither retain payloads nor trust dispatcher scope."""
    identity: dict[str, object] = {"job_id": int(job_id), "request_id": f"job-{job_id}", "source": "durable_worker"}
    try:
        row = db.execute(
            "SELECT update_id,chat_id,session_id,kind,attempts FROM jobs WHERE job_id=?", (int(job_id),)
        ).fetchone()
        if row:
            update_id, chat_id, session_id, kind, attempts = row
            count = max(0, int(attempts or 0))
            identity.update(
                chat_id=str(chat_id),
                session_id=str(session_id),
                kind=str(kind),
                attempt=count + 1,
                recovered=count > 0,
            )
            if int(update_id) >= 0:
                identity.update(request_id=f"tg-{int(update_id)}", update_id=int(update_id))
    except (sqlite3.Error, ValueError, TypeError):
        # Minimal/partially initialized stores must retain the original worker behavior.
        pass
    return clean_fields(identity)


class DurableWorkerGuard:
    """Requeue not-yet-running durable work after transient SQLite failure."""

    def __init__(
        self,
        open_requeue_connection: Callable[..., sqlite3.Connection],
        *,
        sleep: Callable[[float], object] = time.sleep,
        delays: tuple[float, ...] = (0.0, 0.25, 1.0),
        app_settings: AppSettings | None = None,
    ) -> None:
        self._open_requeue_connection = open_requeue_connection
        self._sleep = sleep
        self._delays = tuple(delays)
        self._app_settings = app_settings

    @staticmethod
    def _transient(exc: BaseException) -> bool:
        if not isinstance(exc, sqlite3.OperationalError):
            return False
        text = str(exc).casefold()
        return "locked" in text or "busy" in text

    @staticmethod
    def _database_path(db: sqlite3.Connection) -> Path | None:
        try:
            row = db.execute("PRAGMA database_list").fetchone()
        except sqlite3.Error:
            return None
        if not row or len(row) < 3 or not row[2]:
            return None
        return Path(str(row[2])).expanduser().resolve()

    def _requeue(
        self,
        job_id: int,
        exc: BaseException,
        database_path: Path | None,
    ) -> None:
        if not self._transient(exc) or database_path is None:
            return

        last_error = f"worker database startup failed: {exc}"[:1000]
        for delay in self._delays:
            if delay:
                self._sleep(delay)
            connection = None
            try:
                connection = self._open_requeue_connection(
                    database_path,
                    timeout=10.0,
                )
                cursor = connection.execute(
                    "UPDATE jobs SET state='queued', last_error=?, updated_at=? "
                    "WHERE job_id=? AND state IN ('queued','scheduled')",
                    (last_error, time.time(), int(job_id)),
                )
                connection.commit()
                rowcount = getattr(cursor, "rowcount", None)
                outcome = {"accepted": rowcount > 0} if type(rowcount) is int else {}
                event("job.requeue", job_id=int(job_id), reason="database_locked", **outcome)
                logging.warning(
                    "Requeued durable job %s after transient DB startup failure",
                    job_id,
                )
                return
            except sqlite3.OperationalError:
                logging.warning(
                    "Could not yet requeue durable job %s",
                    job_id,
                    exc_info=True,
                )
            finally:
                if connection is not None:
                    connection.close()

        event("job.recovery_deferred", level=logging.WARNING, job_id=int(job_id), reason="database_locked")
        logging.error(
            "Durable job %s remains recoverable on restart after DB startup failure",
            job_id,
        )

    def prepare(
        self,
        db: sqlite3.Connection,
        job_id: int,
        worker: Callable[..., T],
    ) -> Callable[..., T]:
        database_path = self._database_path(db)
        identity = _job_identity(db, int(job_id))
        app_settings = self._app_settings
        observed = app_settings is not None and performance_enabled(app_settings=app_settings)
        queued_at = None
        if observed:
            try:
                row = db.execute(
                    "SELECT CASE WHEN attempts=0 THEN created_at ELSE updated_at END FROM jobs WHERE job_id=?",
                    (int(job_id),),
                ).fetchone()
                queued_at = float(row[0]) if row else None
            except sqlite3.Error:
                # Optional telemetry must not prevent otherwise valid dispatch.
                pass

        @functools.wraps(worker)
        def guarded_worker(*worker_args: Any) -> T:
            with diagnostic_scope(inherit=False, **identity):
                started = time.monotonic()
                status = "failed"
                event("job.worker_start")
                try:
                    if app_settings is not None and observed:
                        with operation_scope(int(job_id), app_settings=app_settings, queued_at=queued_at):
                            with perf_span("operation_execution", app_settings=app_settings):
                                result = worker(*worker_args)
                    else:
                        result = worker(*worker_args)
                    status = "succeeded"
                    return result
                except BaseException as exc:
                    event("job.worker_error", level=logging.WARNING, error_type=type(exc).__name__, exc_info=True)
                    self._requeue(int(job_id), exc, database_path)
                    raise
                finally:
                    event(
                        "job.worker_finish", status=status, elapsed_ms=max(0, int((time.monotonic() - started) * 1000))
                    )

        return bind_worker(guarded_worker, identity=identity)
