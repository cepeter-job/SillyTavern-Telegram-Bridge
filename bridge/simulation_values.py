"""Shared normalization and storage budgets for simulation values."""

from __future__ import annotations

from typing import Any

KINDS = frozenset({"relationship", "agenda", "actor", "faction", "quest", "foreshadowing", "task"})
MAX_ITEMS = 64
MAX_NAME = 160


def text(value: Any, maximum: int = 1000) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).encode()[:maximum].decode("utf-8", errors="ignore")


def key(value: Any) -> str:
    return text(value, MAX_NAME).casefold()


def integer(value: Any, low: int, high: int, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    return max(low, min(high, parsed))


def boolean(value: Any) -> bool:
    if value is None:
        return False
    if not isinstance(value, bool):
        raise ValueError("Simulation flags must be JSON booleans")
    return value


def modifier_entry(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        value = {"name": value}
    if not isinstance(value, dict):
        return None
    name = text(value.get("name"), 120)
    if not name:
        return None
    return {
        "name": name,
        "domain": text(value.get("domain") or "any", 40).casefold(),
        "modifier": integer(value.get("modifier"), -2, 2),
    }


def merge_named_entries(current: list[dict[str, Any]], adds: Any, removes: Any) -> list[dict[str, Any]]:
    items = {key(item.get("name")): dict(item) for item in current if isinstance(item, dict) and key(item.get("name"))}
    for item in removes or []:
        name = key(item.get("name") if isinstance(item, dict) else item)
        items.pop(name, None)
    for item in adds or []:
        normalized = modifier_entry(item)
        if normalized:
            items[key(normalized["name"])] = normalized
    if len(items) > MAX_ITEMS:
        raise ValueError("Simulation actor collection limit reached")
    return list(items.values())


def relationship_tier(bond: int) -> str:
    for maximum, name in ((-3, "hostile"), (2, "neutral"), (7, "warmth"), (15, "trust")):
        if bond <= maximum:
            return name
    return "love"
