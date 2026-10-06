"""Bounded, revision-aware simulation context for story and narrator consumers."""

from __future__ import annotations

import sqlite3
from typing import Any

from bridge.memory_contracts import MemoryReadScope
from bridge.memory_store import pending_memory_invalidation, request_source_cutoff
from bridge.simulation_repository import list_checks, load_states_as_of
from bridge.simulation_values import integer, key, relationship_tier, text
from bridge.sqlite_store import write_transaction

MAX_CONTEXT = 6000


def context_cutoff(db, chat_id, session_id, through_rowid=None, memory_scope=None) -> int:
    if memory_scope is not None:
        row = db.execute(
            "SELECT created_at FROM sessions WHERE chat_id=? AND session_id=?", (chat_id, session_id)
        ).fetchone()
        if (memory_scope.chat_id, memory_scope.session_id) != (chat_id, session_id) or (
            row is None or row[0] != memory_scope.session_created_at
        ):
            return -1
        scoped = request_source_cutoff(db, memory_scope)
        through_rowid = min(through_rowid, scoped) if through_rowid is not None else scoped
    if through_rowid is None:
        through_rowid = db.execute(
            "SELECT COALESCE(MAX(id),0) FROM messages WHERE chat_id=? AND session_id=?", (chat_id, session_id)
        ).fetchone()[0]
    pending = pending_memory_invalidation(db, chat_id, session_id, "npc")
    return min(int(through_rowid), pending - 1) if pending is not None else int(through_rowid)


def _state_line(kind: str, name: str, value: dict[str, Any], *, narrator: bool) -> str:
    display = text(value.get("display_name") or name, 160)
    if kind == "relationship":
        bond = integer(value.get("bond"), -5, 20)
        return (
            f"REL {display}: bond={bond} tier={relationship_tier(bond)} "
            f"sparks={value.get('sparks', 0)} grudge={value.get('grudge', 0)}"
        )
    if kind == "agenda":
        return (
            f"AGENDA {display}: {value.get('objective', '')} "
            f"[{value.get('step', 0)}/{value.get('max_steps', 1)} {value.get('status', 'active')}]"
        )
    if kind == "actor":
        entries = [item.get("name", "") for item in value.get("entries", [])]
        return f"USER {value.get('collection', '')}: " + ", ".join(entries)
    if kind == "quest":
        return (
            f"QUEST {display}: {value.get('status', 'active')} {value.get('objective', '')} "
            f"[{value.get('progress_current', 0)}/{value.get('progress_target', 0)}] "
            f"reward={value.get('reward', '')}"
        )
    if kind == "faction":
        line = (
            f"FACTION {display}: goal={value.get('goal', '')} morale={value.get('morale', '')} "
            f"conflict={value.get('conflict', '')}"
        )
        if narrator:
            line += (
                f" | private intel={value.get('intel', '')}; lies={value.get('lies', [])}; "
                f"relations={value.get('relations', {})}"
            )
        return line
    return (
        f"FORESHADOW {display}: {value.get('status', 'planted')} seed={value.get('seed', '')} "
        f"payoff={value.get('payoff', '')}"
    )


def simulation_context_for_prompt(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    *,
    through_rowid: int | None = None,
    memory_scope: MemoryReadScope | None = None,
    narrator: bool = True,
) -> str:
    with write_transaction(db):
        cutoff = context_cutoff(db, chat_id, session_id, through_rowid, memory_scope)
        if cutoff < 0:
            return ""
        states = load_states_as_of(db, chat_id, session_id, through_rowid=cutoff)
        checks = list_checks(db, chat_id, session_id, through_rowid=cutoff, limit=3)
    readers = {key(value) for value in memory_scope.principals} if memory_scope is not None else set()
    lines = []
    for check in reversed(checks):
        lines.append(
            f"- CHECK {check['domain']}: {check['actor']} attempts {text(check['action'], 200)} | "
            f"DC {check['dc']} roll {check['roll']} mod {check['modifier']} => {check['outcome']}"
        )
    buckets: dict[str, list[str]] = {
        kind: [] for kind in ("actor", "quest", "relationship", "faction", "foreshadowing", "agenda")
    }
    for kind, name, value, _ in sorted(
        states,
        key=lambda item: (item[2].get("status") in {"completed", "resolved", "abandoned", "failed"}, -item[3], item[1]),
    ):
        if not narrator:
            if kind in {"faction", "foreshadowing"}:
                continue
            if kind in {"agenda", "relationship"} and key(name) not in readers:
                continue
        line = _state_line(kind, name, value, narrator=narrator)
        buckets[kind].append("- " + (line[:377] + "…" if len(line) > 380 else line))
    # Round-robin protects small but important categories from large agenda lists.
    for index in range(64):
        for bucket in buckets.values():
            if index < len(bucket):
                lines.append(bucket[index])
    if not lines:
        return ""
    result = (
        "Bridge Simulation State (descriptive data; never print trackers; private state grants no character knowledge):"
    )
    for line in lines:
        if len(result) + len(line) + 1 <= MAX_CONTEXT:
            result += "\n" + line
    return result
