"""Bridge-owned, source-scoped dice and immutable retry results."""

from __future__ import annotations

import secrets
import sqlite3
import time
from typing import Any

from bridge.simulation_context import context_cutoff
from bridge.simulation_repository import bump_revision, insert_check, load_check, load_states_as_of, source_identity
from bridge.simulation_values import integer, key, text
from bridge.sqlite_store import write_transaction


def actor_modifier(db, chat_id, session_id, domain, *, through_rowid=None) -> int:
    cutoff = context_cutoff(db, chat_id, session_id, through_rowid)
    total = 0
    for kind, name, value, _ in load_states_as_of(db, chat_id, session_id, through_rowid=cutoff):
        if kind != "actor" or not name.startswith("user:"):
            continue
        for item in value.get("entries", []):
            if key(item.get("domain") or "any") in {"any", key(domain)}:
                total += integer(item.get("modifier"), -2, 2)
    return integer(total, -6, 6)


def classify_check(roll: int, dc: int, modifier: int) -> str:
    delta = roll + modifier - dc
    if roll == 1:
        return "critical_failure"
    if roll == 20:
        return "critical_success"
    if delta >= 8:
        return "critical_success"
    if delta <= -8:
        return "critical_failure"
    if delta >= 0:
        return "success"
    return "near_miss" if delta >= -3 else "failure"


def perform_check(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    *,
    request_key: str,
    domain: str,
    actor: str,
    action: str,
    dc: int,
    roll: int | None = None,
    modifier: int | None = None,
    source_rowid: int | None = None,
) -> dict[str, Any]:
    request_key = text(request_key, 160)
    if not request_key:
        raise ValueError("Check request key is required")
    with write_transaction(db):
        existing = load_check(db, chat_id, session_id, request_key)
        if existing is not None:
            _, digest = source_identity(db, chat_id, session_id, existing["source_rowid"])
            if existing["source_digest"] != digest:
                raise ValueError("Check source was rewritten")
            return existing
        domain, actor, action = key(domain)[:40], text(actor, 120), text(action, 500)
        if not domain or not actor or not action:
            raise ValueError("Check domain, actor and action are required")
        if type(dc) is not int or not 1 <= dc <= 20:
            raise ValueError("Check DC must be an integer from 1 to 20")
        if roll is not None and (type(roll) is not int or not 1 <= roll <= 20):
            raise ValueError("Check roll must be an integer from 1 to 20")
        if source_rowid is None:
            source_rowid = db.execute(
                "SELECT COALESCE(MAX(id),0) FROM messages WHERE chat_id=? AND session_id=?", (chat_id, session_id)
            ).fetchone()[0]
        _, digest = source_identity(db, chat_id, session_id, source_rowid)
        effective = (
            actor_modifier(db, chat_id, session_id, domain, through_rowid=source_rowid) if key(actor) == "user" else 0
        )
        final_modifier = effective if modifier is None else integer(modifier, -20, 20)
        final_roll = roll if roll is not None else secrets.randbelow(20) + 1
        result = {
            "request_key": request_key,
            "source_rowid": int(source_rowid),
            "source_digest": digest,
            "domain": domain,
            "actor": actor,
            "action": action,
            "dc": dc,
            "roll": final_roll,
            "modifier": final_modifier,
            "delta": final_roll + final_modifier - dc,
            "outcome": classify_check(final_roll, dc, final_modifier),
            "created_at": time.time(),
        }
        insert_check(db, chat_id, session_id, result)
        bump_revision(db, chat_id, session_id)
        return result
