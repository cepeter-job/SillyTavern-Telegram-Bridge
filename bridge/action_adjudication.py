"""Admit a bounded action proposal, roll once, then bind to a committed user turn."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any

from bridge.action_contracts import action_result_message, parse_action_proposal
from bridge.action_prompt import propose_action
from bridge.action_repository import (
    abandon_preflight,
    delete_preflight,
    finish_preflight,
    load_preflight,
    preflight_owned,
    read_action_scope,
    reserve_preflight,
)
from bridge.closed_session_guard import guard_story_mutation
from bridge.metadata import get_meta, set_meta
from bridge.narrative_values import NARRATIVE_STEERING_PREFIX
from bridge.provider_port import ProviderPort
from bridge.repository_contracts import require_active_transaction
from bridge.settings import AppSettings
from bridge.simulation_checks import classify_check, perform_check
from bridge.simulation_context import context_cutoff
from bridge.simulation_repository import list_checks, load_check, load_states_as_of, source_identity
from bridge.simulation_values import integer, key
from bridge.sqlite_store import write_transaction


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def action_mode(db: sqlite3.Connection, chat_id: str, session_id: str) -> str:
    value = get_meta(db, f"action_checks:{chat_id}:{session_id}", "auto")
    return value if value in {"auto", "director", "manual"} else "manual"


def set_action_mode(db: sqlite3.Connection, chat_id: str, session_id: str, mode: str) -> None:
    if mode not in {"auto", "director", "manual"}:
        raise ValueError("Use /check mode auto, director, or manual.")
    set_meta(db, f"action_checks:{chat_id}:{session_id}", mode)


@dataclass(frozen=True)
class ActionTurn:
    chat_id: str = ""
    session_id: str = ""
    request_key: str = ""
    scope: tuple[float, int, str] | None = None
    input_digest: str = ""
    receipt: dict[str, Any] | None = None

    def messages(self, messages: list[dict]) -> list[dict]:
        addition = action_result_message(self.receipt) if self.receipt is not None else None
        return [*messages, addition] if addition is not None else messages

    def validate(self, db: sqlite3.Connection) -> None:
        if self.scope is not None:
            guard_story_mutation(db, self.chat_id, self.session_id)
            if read_action_scope(db, self.chat_id, self.session_id) != self.scope:
                raise ValueError("The story changed during action generation; the result was not committed.")

    def bind(self, db: sqlite3.Connection, source_rowid: int) -> None:
        if not self.request_key or self.receipt is None:
            return
        require_active_transaction(db)
        row = db.execute(
            "SELECT role,content FROM messages WHERE chat_id=? AND session_id=? AND id=?",
            (self.chat_id, self.session_id, source_rowid),
        ).fetchone()
        if row is None or row[0] != "user" or _digest(row[1]) != self.input_digest:
            raise ValueError("Action result must bind to its exact admitted user source")
        saved = load_preflight(db, self.chat_id, self.session_id, self.request_key)
        if saved is None or not saved[3] or json.loads(saved[3]) != self.receipt:
            raise ValueError("The saved action receipt is no longer available")
        if self.receipt["decision"] == "check":
            result = self.receipt
            perform_check(
                db,
                self.chat_id,
                self.session_id,
                request_key=self.request_key,
                domain=result["domain"],
                actor="user",
                action=result["action"],
                dc=result["dc"],
                roll=result["roll"],
                modifier=result["modifier"],
                source_rowid=source_rowid,
            )
        delete_preflight(db, self.chat_id, self.session_id, self.request_key)


def _effects(db: sqlite3.Connection, chat_id: str, session_id: str, cutoff: int) -> list[dict]:
    return [
        dict(item)
        for kind, name, value, _ in load_states_as_of(db, chat_id, session_id, through_rowid=cutoff)
        if kind == "actor" and name.startswith("user:")
        for item in value.get("entries", [])
    ]


def locked_action_messages(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    messages: list[dict],
    source_rowid: int,
) -> list[dict]:
    cutoff = context_cutoff(db, chat_id, session_id, source_rowid)
    for check in reversed(list_checks(db, chat_id, session_id, through_rowid=cutoff, limit=8)):
        # A /check user row is narrated by the immediately following ordinary turn.
        if check["source_rowid"] != source_rowid:
            if (
                not check["request_key"].startswith("check:")
                or db.execute(
                    "SELECT 1 FROM messages WHERE chat_id=? AND session_id=? AND role='assistant' AND id>? AND id<?",
                    (chat_id, session_id, check["source_rowid"], source_rowid),
                ).fetchone()
            ):
                continue
        try:
            if source_identity(db, chat_id, session_id, check["source_rowid"])[1] != check["source_digest"]:
                continue
        except ValueError:
            continue
        addition = action_result_message(dict(check, decision="check"))
        if addition is not None:
            messages = [*messages, addition]
    return messages


def prepare_action_turn(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict,
    action: str,
    identity: str | None,
    *,
    provider_port: ProviderPort,
    app_settings: AppSettings,
    actor_id: str = "",
    through_rowid: int | None = None,
) -> ActionTurn:
    if db.in_transaction:
        raise RuntimeError("Action preflight requires committed source")
    if not identity or not action.strip() or action.lstrip().startswith(("/", NARRATIVE_STEERING_PREFIX.rstrip())):
        return ActionTurn()
    session_id = str(session["session_id"])
    guard_story_mutation(db, chat_id, session_id)
    token = uuid.uuid4().hex
    with write_transaction(db):
        scope = read_action_scope(db, chat_id, session_id)
        if scope is None:
            raise ValueError("The action session no longer exists")
        request_key = "auto:" + _digest(json.dumps([scope[0], identity, actor_id, action], ensure_ascii=False))
        saved = load_preflight(db, chat_id, session_id, request_key)
        if saved is not None and saved[3]:
            if saved[0] != scope[2]:
                raise ValueError("The story changed since this action was admitted; submit a new action.")
            return ActionTurn(chat_id, session_id, request_key, scope, saved[4], json.loads(saved[3]))
        if load_check(db, chat_id, session_id, request_key) is not None:
            raise ValueError("This action is already committed; recover its saved reply instead.")
        cutoff = min(scope[1], max(0, through_rowid)) if through_rowid is not None else scope[1]
        # /check admits a user row and deliberately awaits the following narration.
        for check in list_checks(
            db, chat_id, session_id, through_rowid=context_cutoff(db, chat_id, session_id, cutoff), limit=1
        ):
            if check["source_rowid"] == scope[1] and check["request_key"].startswith("check:"):
                if source_identity(db, chat_id, session_id, scope[1])[1] == check["source_digest"]:
                    return ActionTurn(chat_id, session_id, scope=scope, receipt=dict(check, decision="check"))
        mode = action_mode(db, chat_id, session_id)
        if mode == "manual":
            return ActionTurn()
        if len(action.encode()) > 12000:
            raise ValueError(
                "Automatic checks accept at most 12000 bytes; shorten this turn or use /check mode manual."
            )
        effects = _effects(db, chat_id, session_id, context_cutoff(db, chat_id, session_id, cutoff))
        reserve_preflight(db, chat_id, session_id, request_key, scope[2], _digest(action), token, time.time())
    try:
        try:
            raw, evidence = propose_action(
                db,
                chat_id,
                session,
                action,
                cutoff,
                mode,
                provider_port=provider_port,
                app_settings=app_settings,
                effects=effects,
            )
        except Exception:
            raise ValueError(
                "Action evaluation is unavailable. Retry this turn or choose /check mode manual."
            ) from None
        proposal = parse_action_proposal(raw, evidence)
        with write_transaction(db):
            guard_story_mutation(db, chat_id, session_id)
            if read_action_scope(db, chat_id, session_id) != scope or action_mode(db, chat_id, session_id) != mode:
                raise ValueError("The story or check mode changed during action evaluation; no roll was accepted.")
            if not preflight_owned(db, chat_id, session_id, request_key, token, time.time()):
                raise ValueError("The action lease changed during evaluation; no roll was accepted.")
            receipt = dict(proposal, model_role=mode)
            if proposal["decision"] == "check":
                modifier = integer(
                    sum(
                        integer(item.get("modifier"), -2, 2)
                        for item in effects
                        if key(item.get("domain") or "any") in {"any", key(proposal["domain"])}
                    ),
                    -6,
                    6,
                )
                roll = secrets.randbelow(20) + 1
                receipt.update(
                    action=proposal["focus"],
                    roll=roll,
                    modifier=modifier,
                    delta=roll + modifier - proposal["dc"],
                    outcome=classify_check(roll, proposal["dc"], modifier),
                )
            finish_preflight(db, chat_id, session_id, request_key, token, receipt)
        return ActionTurn(chat_id, session_id, request_key, scope, _digest(action), receipt)
    except BaseException:
        with write_transaction(db):
            abandon_preflight(db, chat_id, session_id, request_key, token)
        raise
