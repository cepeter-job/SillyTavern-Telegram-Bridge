"""Pure repository contracts and transaction preconditions."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class NpcEntity:
    npc_id: int
    chat_id: str
    session_id: str
    canonical_name: str
    display_name: str
    aliases: tuple[str, ...]
    first_seen_rowid: int
    last_seen_rowid: int
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class NpcFieldState:
    npc_id: int
    field_key: str
    value: Any
    field_mode: str
    visibility: str
    known_by: tuple[str, ...]
    updated_rowid: int
    updated_at: float


@dataclass(frozen=True)
class NpcFieldChange:
    change_id: int
    npc_id: int
    field_key: str
    operation: str
    before: NpcFieldState | None
    after: NpcFieldState | None
    source_rowid: int
    created_at: float


def require_active_transaction(db: sqlite3.Connection) -> None:
    if not db.in_transaction:
        raise RuntimeError("repository write requires an active caller-owned transaction")
