"""Shared immutable domain types for the session-scoped NPC Bank."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bridge.repository_contracts import NpcEntity, NpcFieldChange, NpcFieldState

__all__ = [
    "NpcApplyResult",
    "NpcEntity",
    "NpcExtractionGroup",
    "NpcFieldChange",
    "NpcFieldState",
    "NpcOperation",
]


@dataclass(frozen=True)
class NpcOperation:
    field_key: str
    operation: str
    value: Any
    field_mode: str
    visibility: str
    known_by: tuple[str, ...] = ()


@dataclass(frozen=True)
class NpcExtractionGroup:
    name: str
    aliases: tuple[str, ...]
    operations: tuple[NpcOperation, ...]


@dataclass(frozen=True)
class NpcApplyResult:
    applied: int
    rejected: int
    npc_id: int = 0
