"""Conservative identity reuse and task-step reconciliation for canonical trackers."""

from __future__ import annotations

import sqlite3
from typing import Any

from bridge.simulation_repository import list_states, load_state
from bridge.simulation_values import key


def resolve_named_identity(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    kind: str,
    incoming_key: str,
    item: dict[str, Any],
) -> tuple[str, dict[str, Any] | None]:
    current = load_state(db, chat_id, session_id, kind, incoming_key)
    if current:
        return incoming_key, dict(current[0])
    if kind not in {"task", "quest"} or not (objective := key(item.get("objective"))):
        return incoming_key, None
    signature = "".join(char for char in incoming_key if char.isalnum())
    candidates = []
    for _, name, value, _, _ in list_states(db, chat_id, session_id, kind=kind):
        if "".join(char for char in name if char.isalnum()) != signature:
            continue
        if key(value.get("objective")) != objective:
            continue
        if kind == "quest" and any(
            item.get(link) and value.get(link) and item[link] != value[link] for link in ("arc_id", "thread_id")
        ):
            continue
        candidates.append((name, dict(value)))
    if len(candidates) > 1:
        raise ValueError("Ambiguous tracker identity requires explicit correction")
    # Reuse a unique established ID; do not rename history, sum progress or guess paraphrases.
    return candidates[0] if candidates else (incoming_key, None)


def reconcile_task_steps(value: dict[str, Any]) -> None:
    completed = {key(step).rstrip(".!?") for step in value.get("completed_steps", [])}
    if "pending_steps" in value:
        value["pending_steps"] = [step for step in value["pending_steps"] if key(step).rstrip(".!?") not in completed]
