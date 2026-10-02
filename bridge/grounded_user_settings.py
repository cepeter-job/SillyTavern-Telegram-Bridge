"""Validated, session-scoped Grounded User preference and prompt policy."""

from __future__ import annotations

import sqlite3

from bridge.metadata import get_meta

GROUNDED_USER_POLICY = (
    "Keep the user grounded in established story facts. "
    "Respect explicit established advantages, status, abilities, and relationships. "
    "Do not grant the user unearned competence, authority, knowledge, admiration, attraction, "
    "protection, or plot importance. Success, failure, costs, and consequences should follow "
    "established capability, preparation, circumstances, opposition, and prior events. "
    "NPCs retain independent goals, opinions, loyalties, and preferences. They do not automatically "
    "admire, trust, fear, obey, forgive, romance, protect, or defer to the user. "
    "Do not make the user the center of unrelated events merely because they are the user. "
    "Do not force failure, humiliation, weakness, or punishment merely to oppose the user."
)


def normalize_grounded_user(value: str | None) -> str:
    normalized = str(value or "").strip().casefold()
    normalized = {
        "true": "on",
        "yes": "on",
        "enabled": "on",
        "1": "on",
        "false": "off",
        "no": "off",
        "disabled": "off",
        "0": "off",
    }.get(normalized, normalized)
    if normalized not in {"on", "off"}:
        raise ValueError("use on or off")
    return normalized


def grounded_user_enabled(value: str | None) -> bool:
    return normalize_grounded_user(value or "off") == "on"


def grounded_user_key(chat_id: str, session_id: str) -> str:
    return f"grounded_user:{chat_id}:{session_id}"


def session_grounded_user(db: sqlite3.Connection, chat_id: str, session_id: str) -> str:
    try:
        return normalize_grounded_user(get_meta(db, grounded_user_key(chat_id, session_id), "off"))
    except ValueError:
        return "off"


def grounded_user_policy(value: str | None) -> str:
    return GROUNDED_USER_POLICY if grounded_user_enabled(value) else ""
