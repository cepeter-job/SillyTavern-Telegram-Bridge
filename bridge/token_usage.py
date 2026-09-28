"""Application-owned usage persistence, called only after provider I/O."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from contextlib import closing

from bridge.sqlite_store import write_transaction
from bridge.token_usage_repository import insert_event, prune_events
from bridge.token_usage_values import UsageEvent

RETENTION_DAYS = 90


def record_usage(event: UsageEvent, *, db_factory: Callable[[], sqlite3.Connection]) -> None:
    def total(field: str) -> int | None:
        values = [getattr(reading, field) for reading in event.readings if getattr(reading, field) is not None]
        return sum(values) if values else None

    now = time.time()
    with closing(db_factory()) as db:
        with write_transaction(db):
            prune_events(db, now - RETENTION_DAYS * 86400)
            insert_event(
                db,
                chat_id=event.scope.chat_id,
                session_id=event.scope.session_id,
                model=event.model[:256],
                purpose=event.scope.purpose,
                created_at=now,
                status=event.status,
                elapsed_ms=event.elapsed_ms,
                input_tokens=total("input_tokens"),
                output_tokens=total("output_tokens"),
                total_tokens=total("total_tokens"),
                cached_tokens=total("cached_tokens"),
                reasoning_tokens=total("reasoning_tokens"),
                reported=any(u.reported for u in event.readings),
                complete=bool(event.readings)
                and all(u.complete for u in event.readings)
                and event.status == "succeeded",
            )
