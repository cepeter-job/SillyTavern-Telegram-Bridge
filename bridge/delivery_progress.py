"""Short atomic delivery writes and one canonical completion query."""

import sqlite3

from bridge.delivery_repository import (
    assistant_source,
    begin_progress,
    checkpoint_progress,
    has_delivery_owner,
    has_progress,
    is_complete,
    matching_progress,
)
from bridge.operation_repository import delivery_operation_valid
from bridge.sqlite_store import write_transaction
from bridge.telegram_output import telegram_safe_output
from bridge.turn_delivery_repository import bind_turn_delivery, turn_delivery_target


class DeliveryFailure(RuntimeError):
    """The reply exists locally but transport/checkpoint delivery is incomplete."""


class DeliveryIntentAmbiguous(DeliveryFailure):
    """Historical delivery evidence cannot safely identify its original target."""


class DeliveryTargetExpired(DeliveryFailure):
    """A committed reply was deleted or replaced; replay must not resurrect it."""


def delivery_complete(db: sqlite3.Connection, rowid: int) -> bool:
    return is_complete(db, rowid)


def delivery_has_owner(db: sqlite3.Connection, rowid: int) -> bool:
    return has_delivery_owner(db, rowid)


def prepare_progress(
    db: sqlite3.Connection, rowid: int, payload: str, *, expected_job_id: int | str | None = None
) -> tuple[str, list[int], bool, str]:
    with write_transaction(db):
        _validate_expected_turn(db, expected_job_id, rowid, payload)
        source = assistant_source(db, rowid)
        if source is None:
            raise DeliveryTargetExpired("Saved delivery target was deleted or replaced")
        progress = matching_progress(db, rowid)
        if progress is not None:
            return *progress, source
        if has_progress(db, rowid):
            raise DeliveryTargetExpired("Saved delivery source changed; explicit replacement is required")
        try:
            begin_progress(db, rowid, payload)
        except ValueError as exc:
            raise DeliveryTargetExpired("Saved delivery target was deleted or replaced") from exc
        return payload, [], False, source


def checkpoint(
    db: sqlite3.Connection,
    rowid: int,
    ids: list[int],
    *,
    complete: bool = False,
    expected_job_id: int | str | None = None,
    expected_source: str | None = None,
    expected_payload: str | None = None,
) -> None:
    with write_transaction(db):
        _validate_expected_turn(db, expected_job_id, rowid, expected_payload)
        try:
            checkpoint_progress(db, rowid, ids, complete, expected_source, expected_payload)
        except ValueError as exc:
            raise DeliveryTargetExpired("Saved delivery target was deleted or replaced") from exc


def _validate_expected_turn(db: sqlite3.Connection, job_id: int | str | None, rowid: int, payload: str | None) -> None:
    target = turn_delivery_target(db, job_id)
    if target is None:
        if job_id is not None and delivery_operation_valid(db, job_id, rowid, payload) is False:
            raise DeliveryTargetExpired("Saved operation target or payload was deleted or replaced")
        return
    if target[5]:
        raise DeliveryIntentAmbiguous(str(target[5]))
    if not target[4] or target[2] != rowid or target[3] != payload:
        raise DeliveryTargetExpired("Saved delivery target or payload was deleted or replaced")


def bind_committed_turn(db: sqlite3.Connection, job_id: int | None, user_rowid: int, rowid: int, payload: str) -> None:
    if job_id is not None:
        payload = telegram_safe_output(payload)
        if bind_turn_delivery(db, job_id, user_rowid, rowid, payload):
            prepare_progress(db, rowid, payload)
