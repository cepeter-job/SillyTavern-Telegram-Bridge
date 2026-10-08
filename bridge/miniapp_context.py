"""Private-chat application context and bounded validation, independent of HTTP."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from typing import Any

from bridge.background import chat_job_lock
from bridge.conversation_lifecycle import conversation_state
from bridge.diagnostic_events import diagnostic_scope, event
from bridge.miniapp_auth import MiniAppIdentity
from bridge.miniapp_errors import MiniAppError
from bridge.request_types import RequestContext


@dataclass(frozen=True)
class MiniAppScope:
    db: Any
    session: dict
    context: RequestContext
    chat_id: str


def text(values: dict, key: str, limit: int = 200, *, required: bool = True) -> str:
    value = values.get(key, "")
    if not isinstance(value, str) or len(value) > limit or "\x00" in value or (required and not value.strip()):
        raise MiniAppError(f"Invalid {key.replace('_', ' ')}.")
    return value.strip()


def require_confirmation(values: dict) -> None:
    if values.get("confirm") is not True:
        raise MiniAppError("Review and explicitly confirm this change.", status=409, code="confirmation")


def digest(values: dict, key: str = "digest") -> str:
    value = text(values, key, 64)
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise MiniAppError("Invalid content revision. Refresh this resource.", status=409, code="stale")
    return value


@contextmanager
def session_scope(services: Any, who: MiniAppIdentity, values: dict, *, write: bool = False) -> Iterator[MiniAppScope]:
    lock = chat_job_lock(who.chat_id) if write else None
    if lock is not None and not lock.acquire(timeout=0.1):
        raise MiniAppError("This chat is busy. Retry after its current operation finishes.", status=409, code="busy")
    try:
        with closing(services.db_factory()) as db:
            session = services.session.ensure(db, who.chat_id, services.config.default_model)
            if (write or "session_id" in values) and values.get("session_id") != session["session_id"]:
                raise MiniAppError("The active session changed. Refresh before continuing.", status=409, code="stale")
            with diagnostic_scope(chat_id=who.chat_id, session_id=session["session_id"]):
                status = "failed"
                event("miniapp.session_start")
                try:
                    yield MiniAppScope(
                        db,
                        session,
                        RequestContext(db, session["session_id"], who.user_id, app_settings=services.config),
                        who.chat_id,
                    )
                    status = "succeeded"
                finally:
                    event("miniapp.session_finish", status=status)

    except MiniAppError:
        raise
    except (ValueError, FileNotFoundError, FileExistsError):
        raise MiniAppError(
            "The resource is unavailable or changed. Refresh and try again.", status=409, code="stale"
        ) from None
    finally:
        if lock is not None:
            lock.release()


def current_session(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    with session_scope(services, who, values) as scope:
        state = conversation_state(scope.db, scope.chat_id, scope.session["session_id"])
        return {"session": scope.session, "started": state.started}
