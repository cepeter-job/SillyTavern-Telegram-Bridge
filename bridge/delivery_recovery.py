"""Bounded retry and accurate feedback for locally committed replies."""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bridge.composition import BridgeServices

import json

from bridge.delivery_port import DeliveryPort
from bridge.delivery_progress import DeliveryIntentAmbiguous, DeliveryTargetExpired
from bridge.delivery_retry_repository import failed_delivery_target
from bridge.generation_recovery import _generation_operation_recovery
from bridge.job_store import finish_job
from bridge.response_delivery import send_reply
from bridge.settings import AppSettings
from bridge.sqlite_store import write_transaction
from bridge.turn_delivery_repository import turn_delivery_target


def handle_delivery_failure(
    services: BridgeServices, db: sqlite3.Connection, chat_id: str, job_id: int | None, exc: BaseException
) -> None:
    if isinstance(exc, (DeliveryTargetExpired, DeliveryIntentAmbiguous)):
        if job_id is not None:
            services.jobs.fail(db, job_id, exc)
        services.telegram.send_text(
            services.config.bot_token,
            chat_id,
            _cancelled_feedback(exc),
        )
        return
    if job_id is not None and services.jobs.retry_delivery(db, job_id, exc):
        logging.warning("Committed reply for job %s queued for delivery retry", job_id)
        return
    if job_id is not None:
        services.jobs.fail(db, job_id, f"delivery incomplete: {exc}")
    services.telegram.send_text(
        services.config.bot_token,
        chat_id,
        "The reply or edited branch was saved, but Telegram delivery is incomplete. "
        "Use /retry to recover delivery; the saved branch remains available.",
    )


def retry_failed_delivery(
    db: sqlite3.Connection,
    token: str,
    chat_id: str,
    session_id: str,
    actor_id: str,
    delivery_port: DeliveryPort,
    *,
    app_settings: AppSettings,
) -> bool:
    target = failed_delivery_target(db, chat_id, session_id)
    if target is None:
        return False
    job_id, encoded_job, kind, rowid, payload = target
    durable_actor = str(json.loads(encoded_job).get("actor_id") or "")
    if not durable_actor or actor_id != durable_actor:
        delivery_port.send_text(token, chat_id, "Only the original actor can retry this saved delivery.")
        return True
    recovery = _generation_operation_recovery(delivery_port)
    try:
        bound = turn_delivery_target(db, job_id)
        if bound is not None:
            _validate_bound_target(bound)
        if kind:
            recovery.target_assistant_row(db, chat_id, session_id, job_id)
            recovery.prepare_delivery(db, token, chat_id, rowid, job_id)
        send_reply(
            token,
            chat_id,
            payload,
            db,
            None if kind in {"greeting", "start_greeting"} else session_id,
            rowid,
            expected_job_id=job_id,
            app_settings=app_settings,
        )
        with write_transaction(db):
            if kind:
                recovery.finish(db, job_id, kind)
            finish_job(db, job_id, "done")
    except (DeliveryTargetExpired, DeliveryIntentAmbiguous) as exc:
        finish_job(db, job_id, "failed", str(exc))
        delivery_port.send_text(token, chat_id, _cancelled_feedback(exc))
    except Exception:
        logging.warning("Saved delivery %s remains available for /retry", job_id, exc_info=True)
        delivery_port.send_text(
            token, chat_id, "Delivery is still incomplete. The saved reply remains available for /retry."
        )
    return True


def resume_committed_turn(services: BridgeServices, db: sqlite3.Connection, job_id: int | None) -> bool:
    target = turn_delivery_target(db, job_id)
    if target is None:
        return False
    _validate_bound_target(target)
    chat_id, session_id, rowid, payload, _, _ = target
    send_reply(
        services.config.bot_token,
        chat_id,
        payload,
        db,
        session_id,
        int(rowid),
        expected_job_id=job_id,
        app_settings=services.config,
    )
    if job_id is not None:
        services.jobs.complete(db, job_id)
    return True


def _validate_bound_target(target: tuple) -> None:
    if target[5]:
        raise DeliveryIntentAmbiguous(str(target[5]))
    if not target[4]:
        raise DeliveryTargetExpired("Saved delivery target was deleted or replaced")


def _cancelled_feedback(exc: BaseException) -> str:
    if isinstance(exc, DeliveryIntentAmbiguous):
        return (
            "The old delivery could not be associated safely with its original turn; recovery was cancelled. "
            "Send a fresh request to continue."
        )
    return "The saved reply was deleted or replaced; delivery recovery was cancelled."
