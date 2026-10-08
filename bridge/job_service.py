from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from bridge.diagnostic_events import diagnostic_scope, event
from bridge.port_contracts import ChatSubmit


@dataclass(frozen=True)
class DurableJob:
    job_id: int
    chat_id: str
    session_id: str
    telegram_message_id: int
    kind: str
    payload: dict[str, object]


@dataclass(frozen=True)
class JobSubmission:
    label: str
    chat_id: str
    worker: Callable[..., None]
    args: tuple[object, ...]


def _record_enqueue(job_id: int, update_id: int, chat_id: str, session_id: str, kind: str) -> None:
    event(
        "job.enqueued",
        request_id=f"tg-{update_id}" if update_id >= 0 else f"job-{job_id}",
        job_id=job_id,
        update_id=update_id,
        chat_id=chat_id,
        session_id=session_id,
        kind=kind,
        status="queued",
    )


@dataclass(frozen=True)
class JobService:
    enqueue_backend: Callable[..., int]
    payload_backend: Callable[..., bool]
    actor_backend: Callable[..., str]
    schedule_backend: Callable[..., bool]
    start_backend: Callable[..., bool]
    finish_backend: Callable[..., bool]
    recover_backend: Callable[..., list[tuple]]
    submit_chat: ChatSubmit
    prepare_worker: Callable[..., Callable[..., None]] | None = None
    delivery_retry_backend: Callable[..., bool] | None = None
    callback_enqueue_backend: Callable[..., int | None] | None = None

    def enqueue(
        self,
        db: sqlite3.Connection,
        update_id: int,
        chat_id: str,
        session_id: str,
        telegram_message_id: int,
        kind: str,
        payload: dict[str, object],
    ) -> int:
        job_id = int(
            self.enqueue_backend(
                db,
                int(update_id),
                str(chat_id),
                str(session_id),
                int(telegram_message_id),
                str(kind),
                payload,
            )
        )
        _record_enqueue(job_id, int(update_id), str(chat_id), str(session_id), str(kind))
        return job_id

    def enqueue_callback(
        self,
        db: sqlite3.Connection,
        update_id: int,
        chat_id: str,
        session_id: str,
        telegram_message_id: int,
        payload: dict[str, object],
    ) -> int | None:
        if self.callback_enqueue_backend is None:
            return self.enqueue(db, update_id, chat_id, session_id, telegram_message_id, "callback", payload)
        result = self.callback_enqueue_backend(
            db,
            int(update_id),
            str(chat_id),
            str(session_id),
            int(telegram_message_id),
            payload,
        )
        if result is not None:
            _record_enqueue(int(result), int(update_id), str(chat_id), str(session_id), "callback")
        else:
            event("job.callback_rejected", update_id=int(update_id), chat_id=str(chat_id), accepted=False)
        return int(result) if result is not None else None

    def submit(
        self,
        db: sqlite3.Connection,
        job_id: int,
        submission: JobSubmission,
    ) -> bool:
        worker = submission.worker
        if self.prepare_worker is not None:
            worker = self.prepare_worker(
                db,
                int(job_id),
                worker,
            )
        accepted = bool(
            self.submit_chat(
                submission.label,
                submission.chat_id,
                worker,
                *submission.args,
                int(job_id),
            )
        )
        event("job.submitted", job_id=int(job_id), label=submission.label, accepted=accepted)
        if accepted:
            scheduled = bool(self.schedule_backend(db, int(job_id)))
            event("job.scheduled", job_id=int(job_id), accepted=scheduled)
        return accepted

    def replace_payload(self, db: sqlite3.Connection, job_id: int, payload: dict[str, object]) -> bool:
        accepted = bool(self.payload_backend(db, int(job_id), payload))
        event("job.payload_replaced", job_id=int(job_id), accepted=accepted)
        return accepted

    def start(self, db: sqlite3.Connection, job_id: int) -> bool:
        accepted = bool(self.start_backend(db, int(job_id)))
        event("job.started", job_id=int(job_id), accepted=accepted)
        return accepted

    def complete(self, db: sqlite3.Connection, job_id: int) -> bool:
        accepted = bool(
            self.finish_backend(
                db,
                int(job_id),
                "done",
                "",
            )
        )
        event("job.completed", job_id=int(job_id), accepted=accepted, status="done" if accepted else "unchanged")
        return accepted

    def fail(
        self,
        db: sqlite3.Connection,
        job_id: int,
        error: object,
    ) -> bool:
        accepted = bool(
            self.finish_backend(
                db,
                int(job_id),
                "failed",
                str(error),
            )
        )
        event("job.failed", job_id=int(job_id), accepted=accepted, status="failed" if accepted else "unchanged")
        return accepted

    def retry_delivery(self, db: sqlite3.Connection, job_id: int, error: object) -> bool:
        accepted = bool(self.delivery_retry_backend(db, int(job_id), error)) if self.delivery_retry_backend else False
        event("delivery.retry_requested", job_id=int(job_id), accepted=accepted)
        return accepted

    def actor_id(
        self,
        db: sqlite3.Connection,
        job_id: int | None,
    ) -> str:
        return str(self.actor_backend(db, job_id) or "")

    def recover(
        self,
        db: sqlite3.Connection,
        resolver: Callable[[DurableJob], JobSubmission | None],
        *,
        recover_running: bool = True,
    ) -> None:
        rows = self.recover_backend(
            db,
            recover_running=recover_running,
        )
        for (
            job_id,
            chat_id,
            session_id,
            message_id,
            kind,
            payload_json,
        ) in rows:
            with diagnostic_scope(
                inherit=False,
                request_id=f"job-{int(job_id)}",
                job_id=int(job_id),
                chat_id=str(chat_id),
                session_id=str(session_id),
                kind=str(kind),
                recovered=True,
                source="durable_recovery",
            ):
                event("job.recovery_started")
                try:
                    payload = json.loads(payload_json)
                    if not isinstance(payload, dict):
                        raise ValueError("job payload must be an object")
                    job = DurableJob(
                        job_id=int(job_id),
                        chat_id=str(chat_id),
                        session_id=str(session_id),
                        telegram_message_id=int(message_id or 0),
                        kind=str(kind),
                        payload=payload,
                    )
                    submission = resolver(job)
                    if submission is None:
                        self.fail(db, job.job_id, "unsupported recovered job kind")
                        event("job.recovery_rejected", reason="unsupported_kind", accepted=False)
                        continue
                    accepted = self.submit(db, job.job_id, submission)
                    event("job.recovery_submitted", accepted=accepted)
                    if not accepted:
                        logging.warning("Could not dispatch recovered %s job %s", job.kind, job.job_id)
                except Exception as exc:
                    self.fail(db, int(job_id), exc)
                    event("job.recovery_failed", level=logging.WARNING, error_type=type(exc).__name__)
                    logging.error("Could not recover job %s", job_id, exc_info=True)
