"""Checkpoint a reset's frozen Telegram deletion set across process restarts."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable

from bridge.metadata import get_meta, set_meta
from bridge.response_delivery import TelegramDeletionReport
from bridge.sqlite_store import write_transaction


def _key(operation_id: int | str) -> str:
    return f"reset_telegram_cleanup:{operation_id}"


def snapshot_reset_cleanup(
    db: sqlite3.Connection, operation_id: int | str | None, chat_id: str, session_id: str, message_ids: list[int]
) -> None:
    if operation_id is not None:
        if not db.in_transaction:
            raise RuntimeError("Reset cleanup snapshot requires the local reset transaction")
        ids = sorted(set(message_ids))
        set_meta(
            db,
            _key(operation_id),
            json.dumps(
                {
                    "chat_id": chat_id,
                    "session_id": session_id,
                    "pending": ids,
                    "total": len(ids),
                    "deleted": 0,
                    "failed": [],
                }
            ),
        )


def finish_reset_cleanup(
    db: sqlite3.Connection,
    token: str,
    chat_id: str,
    session_id: str,
    operation_id: int | str | None,
    fallback_ids: list[int],
    *,
    request: Callable,
) -> TelegramDeletionReport | None:
    """Durability requires an operation ID; legacy direct calls remain best-effort."""
    if operation_id is None:
        ids = sorted(set(fallback_ids))
        state = {"pending": ids, "total": len(ids), "deleted": 0, "failed": []}
    else:
        raw = get_meta(db, _key(operation_id), "")
        if not raw:
            # Legacy local commits have no recoverable ID list. Never sweep a new story.
            return None
        state = json.loads(raw)
        if (
            not isinstance(state, dict)
            or not isinstance(state.get("pending"), list)
            or not isinstance(state.get("failed"), list)
            or any(type(state.get(key)) is not int or state[key] < 0 for key in ("total", "deleted"))
            or any(type(item) is not int or item <= 0 for key in ("pending", "failed") for item in state[key])
        ):
            raise ValueError("Invalid reset cleanup snapshot")
        if state.get("chat_id") != chat_id or state.get("session_id") != session_id:
            raise ValueError("Reset cleanup scope does not match the operation")
    failures: set[str] = set()
    for message_id in list(state["pending"]):
        try:
            request(token, "deleteMessage", {"chat_id": chat_id, "message_id": message_id})
            state["deleted"] += 1
        except Exception as exc:
            # A crash can occur after Telegram deletes the message but before its ACK commits.
            if isinstance(exc, RuntimeError) and str(exc).casefold() == (
                "telegram deletemessage failed: bad request: message to delete not found"
            ):
                state["deleted"] += 1
            else:
                state["failed"].append(message_id)
                failures.add(type(exc).__name__)
        state["pending"].remove(message_id)
        if operation_id is not None:
            with write_transaction(db):
                set_meta(db, _key(operation_id), json.dumps(state))
    report = TelegramDeletionReport(state["total"], state["deleted"], len(state["failed"]))
    if report.failed:
        logging.warning(
            "Telegram message cleanup incomplete: deleted=%d attempted=%d failed=%d error_types=%s",
            report.deleted,
            report.attempted,
            report.failed,
            ",".join(sorted(failures)),
        )
    return report
