"""Bounded restart admission for prepared or interrupted alternate-ending requests."""

from __future__ import annotations

import logging
import sqlite3
import time
from functools import partial
from typing import Any

from bridge.alternate_ending import recover_alternate_ending
from bridge.alternate_ending_memory import seed_alternate_ending_memory
from bridge.background import chat_job_lock, submit_background
from bridge.operation_repository import (
    discard_scoped_operation_payload,
    read_scoped_operation,
    recoverable_branch_operations,
    write_operation_phase,
)
from bridge.settings import AppSettings
from bridge.sqlite_store import db_connect, write_transaction


def _recover_branch_worker(operation_id: str, app_settings: AppSettings, admission: Any) -> None:
    db = None
    chat_lock = None
    locked = False
    try:
        db = db_connect(app_settings=app_settings)
        operation = read_scoped_operation(db, operation_id)
        if operation is None:
            return
        chat = operation["payload"].get("chat_id")
        if not isinstance(chat, str) or not chat:
            raise ValueError("The branch request has no chat identity")
        chat_lock = chat_job_lock(chat)
        locked = chat_lock.acquire(blocking=False)
        if not locked:
            return
        recover_alternate_ending(
            db, operation_id, seed_memory=partial(seed_alternate_ending_memory, app_settings=app_settings)
        )
    except ValueError:
        logging.warning("Alternate-ending recovery paused because its saved request is no longer valid")
        if db is not None:
            with write_transaction(db):
                current = read_scoped_operation(db, operation_id)
                if current and current["state"] == "prepared":
                    write_operation_phase(db, operation_id, "alternate_ending", "failed", time.time())
                    discard_scoped_operation_payload(db, operation_id)
    except Exception as exc:
        logging.warning("Alternate-ending recovery will retry unfinished local work: %s", type(exc).__name__)
    finally:
        if db is not None:
            db.close()
        if locked and chat_lock is not None:
            chat_lock.release()
        admission.release()


def queue_alternate_ending_recovery(db: sqlite3.Connection, *, app_settings: AppSettings) -> int:
    count = 0
    for operation_id in recoverable_branch_operations(db, time.time(), limit=32):
        admission = chat_job_lock(f"alternate:{app_settings.db_file.resolve()}:{operation_id}")
        if not admission.acquire(blocking=False):
            continue
        try:
            submitted = submit_background(
                "alternate_ending_recovery", _recover_branch_worker, operation_id, app_settings, admission
            )
            if submitted:
                count += 1
            else:
                admission.release()
        except Exception:
            admission.release()
            raise
    return count
