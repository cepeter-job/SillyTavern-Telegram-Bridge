"""Ordinary crash-recovery mechanics for durable bridge operations."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass

from bridge.delivery_progress import DeliveryTargetExpired, prepare_progress
from bridge.delivery_repository import matching_progress
from bridge.operation_repository import delivery_user_source
from bridge.telegram_output import telegram_safe_output
from bridge.transcript_repository import assistant_by_row


@dataclass(frozen=True)
class OperationRecovery:
    operation_phase: Callable[..., str]
    begin_operation: Callable[..., bool]
    record_operation: Callable[..., None]
    write_transaction: Callable[[sqlite3.Connection], AbstractContextManager[sqlite3.Connection]]
    get_meta: Callable[..., str]
    telegram_request: Callable[..., object]
    delete_outgoing_message_row: Callable[..., None]
    log_info: Callable[..., None]

    @staticmethod
    def _payload_key(operation_id) -> str:
        return f"operation_payload:{operation_id}"

    def set_payload(self, db, operation_id, payload) -> None:
        if operation_id is None:
            return

        def write():
            db.execute(
                "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
                (
                    self._payload_key(operation_id),
                    json.dumps(payload, separators=(",", ":")),
                ),
            )

        with self.write_transaction(db):
            write()

    def get_payload(self, db, operation_id) -> dict:
        if operation_id is None:
            return {}
        raw = self.get_meta(
            db,
            self._payload_key(operation_id),
            "",
        )
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def finish(self, db, operation_id, kind) -> None:
        if operation_id is None:
            return

        def write():
            self.record_operation(db, operation_id, kind)
            db.execute(
                "DELETE FROM meta WHERE key=?",
                (self._payload_key(operation_id),),
            )

        with self.write_transaction(db):
            write()

    def begin_or_recover(
        self,
        db,
        operation_id,
        kind,
        deliver_recovered,
    ) -> bool:
        if operation_id is None:
            return True
        phase = self.operation_phase(db, operation_id)
        if phase == "applied":
            return False
        if phase == "local_committed":
            deliver_recovered()
            return False
        return bool(self.begin_operation(db, operation_id, kind))

    def record_delivery_target(
        self,
        db: sqlite3.Connection,
        operation_id: int | str | None,
        assistant_rowid: int,
        source_content: str,
        payload: str,
    ) -> None:
        with self.write_transaction(db):
            payload = telegram_safe_output(payload)
            original = self.get_payload(db, operation_id)
            user_source = (
                delivery_user_source(db, assistant_rowid, int(original["user_rowid"]))
                if "user_rowid" in original
                else None
            )
            if user_source is not None:
                original.update(user_source_content=user_source[0], user_message_id=user_source[1])
            self.set_payload(
                db,
                operation_id,
                dict(
                    original,
                    assistant_rowid=assistant_rowid,
                    source_content=source_content,
                    delivery_payload=payload,
                ),
            )
            prepare_progress(db, assistant_rowid, payload, expected_job_id=operation_id)

    def target_assistant_row(
        self, db: sqlite3.Connection, chat_id: str, session_id: str, operation_id: int | str | None
    ) -> tuple[int, str] | None:
        payload = self.get_payload(db, operation_id)
        if "assistant_rowid" not in payload:
            if "user_rowid" in payload:
                user = self.latest_user_row(db, chat_id, session_id)
                if user is None or int(user[0]) != int(payload["user_rowid"]):
                    raise DeliveryTargetExpired("Legacy saved delivery source was deleted or superseded")
            return self.latest_assistant_row(db, chat_id, session_id)
        row = assistant_by_row(db, int(payload["assistant_rowid"]), chat_id, session_id)
        if row is None or ("source_content" in payload and row[1] != payload["source_content"]):
            raise DeliveryTargetExpired("Saved delivery target was deleted or superseded")
        progress = matching_progress(db, int(row[0]))
        if progress is not None and "delivery_payload" in payload and progress[0] != payload["delivery_payload"]:
            raise DeliveryTargetExpired("Saved delivery payload was superseded")
        return row

    @staticmethod
    def message_ids_from_rows(rows) -> list[str]:
        result = []
        for legacy_id, encoded_ids in rows:
            if legacy_id not in {None, ""}:
                result.append(str(legacy_id))
            try:
                decoded = json.loads(encoded_ids or "[]")
            except (TypeError, json.JSONDecodeError):
                decoded = []
            if isinstance(decoded, list):
                result.extend(str(item) for item in decoded if item not in {None, ""})
        return list(dict.fromkeys(result))

    def outgoing_ids_after(
        self,
        db,
        chat_id,
        session_id,
        rowid,
    ) -> list[str]:
        rows = db.execute(
            "SELECT telegram_message_id,telegram_message_ids "
            "FROM messages WHERE chat_id=? AND session_id=? "
            "AND role='assistant' AND rowid>? ORDER BY rowid",
            (chat_id, session_id, int(rowid)),
        ).fetchall()
        return self.message_ids_from_rows(rows)

    def delete_stored_telegram_ids(
        self,
        token,
        chat_id,
        message_ids,
    ) -> None:
        for message_id in message_ids or []:
            try:
                self.telegram_request(
                    token,
                    "deleteMessage",
                    {
                        "chat_id": chat_id,
                        "message_id": int(message_id),
                    },
                )
            except Exception:
                self.log_info(
                    "Recovery cleanup could not delete Telegram message %s",
                    message_id,
                    exc_info=True,
                )

    def prepare_delivery(
        self,
        db,
        token,
        chat_id,
        assistant_rowid,
        operation_id,
    ) -> None:
        try:
            if matching_progress(db, int(assistant_rowid)) is None:
                self.delete_outgoing_message_row(
                    db,
                    token,
                    chat_id,
                    int(assistant_rowid),
                )
        except Exception:
            self.log_info(
                "Recovery could not clear the current assistant delivery",
                exc_info=True,
            )
        payload = self.get_payload(db, operation_id)
        self.delete_stored_telegram_ids(
            token,
            chat_id,
            payload.get("old_message_ids") or [],
        )

    @staticmethod
    def selected_variant_index(
        db,
        chat_id,
        session_id,
        user_rowid,
    ) -> int:
        row = db.execute(
            "SELECT variant_index FROM response_variants "
            "WHERE chat_id=? AND session_id=? AND user_rowid=? "
            "AND selected=1 ORDER BY id DESC LIMIT 1",
            (chat_id, session_id, int(user_rowid)),
        ).fetchone()
        return int(row[0]) if row else 1

    @staticmethod
    def latest_user_row(db, chat_id, session_id):
        return db.execute(
            "SELECT rowid,content FROM messages "
            "WHERE chat_id=? AND session_id=? AND role='user' "
            "ORDER BY rowid DESC LIMIT 1",
            (chat_id, session_id),
        ).fetchone()

    @staticmethod
    def latest_assistant_row(db, chat_id, session_id):
        return db.execute(
            "SELECT rowid,content FROM messages "
            "WHERE chat_id=? AND session_id=? AND role='assistant' "
            "ORDER BY rowid DESC LIMIT 1",
            (chat_id, session_id),
        ).fetchone()
