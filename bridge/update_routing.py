"""Telegram update coordinator and durable completion boundary."""

from __future__ import annotations

import sqlite3
import time

from bridge.composition import BridgeServices
from bridge.diagnostic_events import diagnostic_scope, event
from bridge.job_repository import store_processed_update
from bridge.meta_repository import store_meta_value
from bridge.sqlite_store import write_transaction
from bridge.update_callback_routing import route_callback_update
from bridge.update_message_routing import route_edited_message_update, route_message_update


def complete_update(
    db: sqlite3.Connection,
    update_id: int,
    offset: int,
) -> None:
    def write():
        store_processed_update(db, update_id, time.time())
        store_meta_value(db, "telegram_offset", str(offset))

    with write_transaction(db):
        write()
    event("telegram.update_acknowledged", update_id=update_id)


def route_update(
    services: BridgeServices,
    db: sqlite3.Connection,
    fields: dict,
    update: dict,
    offset: int,
    permitted: frozenset[str],
) -> int:
    update_id = int(update["update_id"])
    with diagnostic_scope(inherit=False, request_id=f"tg-{update_id}", update_id=update_id, source="telegram"):
        started = time.monotonic()
        status = "failed"
        error_fields: dict[str, object] = {}
        event("telegram.update_received")
        try:
            result = _route_update(services, db, fields, update, offset, permitted)
            status = "succeeded"
            return result
        except BaseException as exc:
            error_fields["error_type"] = type(exc).__name__
            raise
        finally:
            event(
                "telegram.update_finish",
                status=status,
                elapsed_ms=max(0, int((time.monotonic() - started) * 1000)),
                **error_fields,
            )


def _route_update(
    services: BridgeServices,
    db: sqlite3.Connection,
    fields: dict,
    update: dict,
    offset: int,
    permitted: frozenset[str],
) -> int:
    update_id = int(update["update_id"])
    next_offset = max(offset, update_id + 1)

    if db.execute(
        "SELECT 1 FROM processed_updates WHERE update_id=?",
        (update_id,),
    ).fetchone():
        event("telegram.update_duplicate")
        complete_update(db, update_id, next_offset)
        return next_offset

    callback = update.get("callback_query")
    if callback:
        route_callback_update(
            services,
            db,
            callback,
            update_id,
            permitted,
        )
        complete_update(db, update_id, next_offset)
        return next_offset

    edited_message = update.get("edited_message")
    if edited_message:
        route_edited_message_update(
            services,
            db,
            edited_message,
            update_id,
            permitted,
        )
        complete_update(db, update_id, next_offset)
        return next_offset

    should_complete = route_message_update(
        services,
        db,
        fields,
        update.get("message") or {},
        update_id,
        permitted,
    )
    if should_complete:
        complete_update(db, update_id, next_offset)
    return next_offset
