"""Explicit checks and durable result delivery without a fabricated assistant story turn."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any

from bridge.check_panels import send_check_menu
from bridge.closed_session_guard import guard_story_mutation
from bridge.conversation_lifecycle import START_REQUIRED, require_started, split_command_text
from bridge.delivery_port import DeliveryPort
from bridge.delivery_progress import DeliveryFailure, DeliveryTargetExpired
from bridge.extension_registry import register_command_route
from bridge.failed_turns import clear_failed_turn
from bridge.job_store import finish_job
from bridge.meta_repository import load_meta_value, store_meta_value
from bridge.operations import begin_operation, operation_phase, record_operation, set_operation_phase
from bridge.provider_port import ProviderPort
from bridge.request_types import RequestContext
from bridge.simulation_checks import perform_check
from bridge.simulation_repository import load_check, source_identity
from bridge.simulation_view import active_tracker_view
from bridge.simulation_view_output import format_tracker_view
from bridge.sqlite_store import write_transaction

_USAGE = "Use /check <domain> <DC> <action>. DC must be an integer from 1 to 20."
_KIND = "simulation_check"


def _parse(command: str) -> tuple[str, int, str]:
    parts = command.split(None, 3)
    if len(parts) != 4 or not parts[2].isascii() or not parts[2].isdigit():
        raise ValueError(_USAGE)
    domain, dc, action = parts[1].casefold(), int(parts[2]), parts[3].strip()
    if not 1 <= dc <= 20 or not 1 <= len(domain.encode()) <= 40 or not 1 <= len(action.encode()) <= 500:
        raise ValueError(_USAGE)
    return domain, dc, action


def _payload_key(operation_id: int | str | None) -> str:
    return f"operation_payload:{operation_id}"


def _load_payload(db: sqlite3.Connection, operation_id: int | str | None) -> dict[str, Any]:
    raw = load_meta_value(db, _payload_key(operation_id), "{}")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise DeliveryTargetExpired("The saved check result is unavailable")
    return value


def _finish(db: sqlite3.Connection, operation_id: int | str | None) -> None:
    with write_transaction(db):
        record_operation(db, operation_id, _KIND)
        db.execute("DELETE FROM meta WHERE key=?", (_payload_key(operation_id),))


def _deliver(
    db: sqlite3.Connection,
    token: str,
    chat_id: str,
    session_id: str,
    actor_id: str,
    operation_id: int | str | None,
    delivery_port: DeliveryPort,
) -> None:
    payload = _load_payload(db, operation_id)
    if (payload.get("chat_id"), payload.get("session_id"), payload.get("actor_id")) != (chat_id, session_id, actor_id):
        raise DeliveryTargetExpired("Saved check ownership no longer matches this request")
    with write_transaction(db):
        check = load_check(db, chat_id, session_id, payload.get("request_key", ""))
        if check is None:
            raise DeliveryTargetExpired("The saved check action was deleted or replaced")
        try:
            _, digest = source_identity(db, chat_id, session_id, check["source_rowid"])
        except ValueError as exc:
            raise DeliveryTargetExpired("The saved check action was deleted or replaced") from exc
        if digest != check["source_digest"] or digest != payload.get("source_digest"):
            raise DeliveryTargetExpired("The saved check action was rewritten")
    try:
        delivery_port.send_text(token, chat_id, payload["delivery_payload"])
    except Exception as exc:
        raise DeliveryFailure("Check result is saved; Telegram delivery failed") from exc
    _finish(db, operation_id)


def _admit(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    actor_id: str,
    operation_id: int | str | None,
    domain: str,
    dc: int,
    action: str,
) -> None:
    with write_transaction(db):
        job = db.execute(
            "SELECT chat_id,session_id,telegram_message_id,payload_json FROM jobs WHERE CAST(job_id AS TEXT)=?",
            (str(operation_id),),
        ).fetchone()
        message_id = None
        if job:
            durable_actor = str(json.loads(job[3]).get("actor_id") or "")
            if job[:2] != (chat_id, session_id) or (durable_actor and durable_actor != actor_id):
                raise ValueError("Check job belongs to another session or actor")
            message_id = job[2]
        existing = db.execute("SELECT kind FROM operations WHERE operation_id=?", (str(operation_id),)).fetchone()
        if existing and existing[0] != _KIND:
            raise ValueError("Operation already belongs to another action")
        if not begin_operation(db, operation_id, _KIND):
            return
        row = db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,telegram_message_id,created_at) "
            "VALUES(?,?,'user',?,?,?)",
            (chat_id, session_id, f"[Check action: {domain}, DC {dc}] {action}", message_id, time.time()),
        )
        if row.lastrowid is None:
            raise RuntimeError("Check action did not receive a source identity")
        check = perform_check(
            db,
            chat_id,
            session_id,
            request_key=f"check:{operation_id}",
            domain=domain,
            actor="user",
            action=action,
            dc=dc,
            source_rowid=row.lastrowid,
        )
        rendered = (
            f"🎲 {domain} check\n{action}\n\n"
            f"d20 {check['roll']} {check['modifier']:+d} = {check['roll'] + check['modifier']} vs DC {dc}\n"
            f"{check['outcome'].replace('_', ' ').capitalize()} (Δ {check['delta']:+d})\n\n"
            "This result is recorded for your next story turn."
        )
        store_meta_value(
            db,
            _payload_key(operation_id),
            json.dumps(
                {
                    "chat_id": chat_id,
                    "session_id": session_id,
                    "actor_id": actor_id,
                    "request_key": check["request_key"],
                    "source_rowid": check["source_rowid"],
                    "source_digest": check["source_digest"],
                    "delivery_payload": rendered,
                },
                ensure_ascii=False,
            ),
        )
        set_operation_phase(db, operation_id, _KIND, "local_committed")


def handle_check_command(
    db: sqlite3.Connection,
    token: str,
    chat_id: str,
    session_id: str,
    command: str,
    actor_id: str,
    operation_id: int | str | None,
    delivery_port: DeliveryPort,
) -> None:
    try:
        domain, dc, action = _parse(command)
    except ValueError:
        delivery_port.send_text(token, chat_id, _USAGE)
        return
    operation_id = operation_id if operation_id is not None else "standalone-check:" + uuid.uuid4().hex
    phase = operation_phase(db, operation_id)
    if phase == "applied":
        return
    if phase == "local_committed":
        _deliver(db, token, chat_id, session_id, actor_id, operation_id, delivery_port)
        return
    if not require_started(db, chat_id, session_id):
        delivery_port.send_text(token, chat_id, START_REQUIRED)
        return
    guard_story_mutation(db, chat_id, session_id)
    _admit(db, chat_id, session_id, actor_id, operation_id, domain, dc, action)
    _deliver(db, token, chat_id, session_id, actor_id, operation_id, delivery_port)


def retry_failed_check(
    db: sqlite3.Connection, token: str, chat_id: str, session_id: str, actor_id: str, delivery_port: DeliveryPort
) -> bool:
    row = db.execute(
        "SELECT j.job_id,j.telegram_message_id FROM jobs j JOIN operations o "
        "ON o.operation_id=CAST(j.job_id AS TEXT) WHERE j.chat_id=? AND j.session_id=? "
        "AND j.state='failed' AND o.kind='simulation_check' AND o.state='local_committed' "
        "ORDER BY j.updated_at DESC,j.job_id DESC LIMIT 1",
        (chat_id, session_id),
    ).fetchone()
    if row is None:
        return False
    payload = _load_payload(db, row[0])
    if actor_id != payload.get("actor_id"):
        delivery_port.send_text(token, chat_id, "Only the original actor can retry this saved check.")
        return True
    try:
        _deliver(db, token, chat_id, session_id, actor_id, row[0], delivery_port)
    except DeliveryTargetExpired as exc:
        _finish(db, row[0])
        finish_job(db, row[0], "failed", "delivery target expired")
        delivery_port.send_text(token, chat_id, str(exc))
    except DeliveryFailure:
        delivery_port.send_text(token, chat_id, "Check delivery is still incomplete. Use /retry again.")
    else:
        finish_job(db, row[0], "done")
        clear_failed_turn(db, chat_id, int(row[1]))
    return True


def _simulation_command_route(
    db: sqlite3.Connection,
    token: str,
    api_key: str,
    model: str,
    fields: dict[str, Any],
    chat_id: str,
    stripped: str,
    command: str,
    session: dict[str, Any],
    session_id: str,
    current_model: str,
    current_persona: str,
    user_name: str,
    operation_id: int | str | None = None,
    *,
    request_context: RequestContext,
    delivery_port: DeliveryPort,
    provider_port: ProviderPort,
) -> bool:
    if command == "/trackers" or command.startswith("/trackers "):
        view = active_tracker_view(db, chat_id, app_settings=request_context.app_settings)
        delivery_port.send_text(token, chat_id, format_tracker_view(view))
        return True
    if command != "/check" and not command.startswith("/check "):
        return False
    if command == "/check":
        send_check_menu(
            token,
            chat_id,
            db,
            session_id,
            request_context=request_context,
        )
        return True
    root, arguments = split_command_text(stripped)
    handle_check_command(
        db, token, chat_id, session_id, root + " " + arguments, request_context.actor_id, operation_id, delivery_port
    )
    return True


def register_simulation_extensions() -> None:
    register_command_route("simulation", _simulation_command_route)
