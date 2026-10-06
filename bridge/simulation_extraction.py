"""Bounded parsing and accumulation for utility-model simulation updates."""

from __future__ import annotations

import json
from typing import Any

from bridge.json_fences import unfence_json

_MAX_ITEMS = 32
_MAX_NAME = 160
_MAX_TEXT = 1000


def _text(value: Any, maximum: int = _MAX_TEXT) -> str:
    return " ".join(str(value or "").split()).strip()[:maximum]


def _integer(value: Any, low: int, high: int, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    return max(low, min(high, parsed))


def _modifier_entry(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        name = _text(value, 120)
        return {"name": name, "domain": "any", "modifier": 0} if name else None
    if not isinstance(value, dict):
        return None
    name = _text(value.get("name"), 120)
    if not name:
        return None
    domain = _text(value.get("domain") or "any", 40).casefold()
    return {"name": name, "domain": domain or "any", "modifier": _integer(value.get("modifier"), -2, 2)}


def _modifier_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value[:_MAX_ITEMS]:
        normalized = _modifier_entry(item)
        if normalized:
            result.append(normalized)
    return result


def _relationships(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result = []
    seen = set()
    for item in value[:_MAX_ITEMS]:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("npc"), _MAX_NAME)
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        result.append(
            {
                "npc": name,
                "bond_delta": _integer(item.get("bond_delta"), -2, 0),
                "sparks_delta": _integer(item.get("sparks_delta"), -2, 2),
                "grudge_delta": _integer(item.get("grudge_delta"), -1, 1),
                "apology": bool(item.get("apology")),
            }
        )
    return result


def _agendas(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result = []
    seen = set()
    for item in value[:_MAX_ITEMS]:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("npc"), _MAX_NAME)
        objective = _text(item.get("objective"), 500)
        key = name.casefold()
        if not name or not objective or key in seen:
            continue
        seen.add(key)
        max_steps = _integer(item.get("max_steps"), 1, 20, 1)
        step = _integer(item.get("step"), 0, max_steps)
        status = _text(item.get("status") or "active", 20).casefold()
        if status not in {"active", "paused", "completed"}:
            status = "active"
        result.append(
            {
                "npc": name,
                "objective": objective,
                "step": step,
                "max_steps": max_steps,
                "location": _text(item.get("location"), 300),
                "status": status,
                "complete": bool(item.get("complete")),
            }
        )
    return result


def _factions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result = []
    seen = set()
    for item in value[:_MAX_ITEMS]:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name"), _MAX_NAME)
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        relations = item.get("relations")
        result.append(
            {
                "name": name,
                "goal": _text(item.get("goal")),
                "intel": _text(item.get("intel")),
                "lies": [_text(part, 240) for part in (item.get("lies") or [])[:_MAX_ITEMS] if _text(part, 240)]
                if isinstance(item.get("lies"), list)
                else [],
                "morale": _text(item.get("morale"), 120),
                "conflict": _text(item.get("conflict")),
                "relations": {
                    _text(k, 120): _text(v, 240)
                    for k, v in list(relations.items())[:_MAX_ITEMS]
                    if _text(k, 120)
                }
                if isinstance(relations, dict)
                else {},
            }
        )
    return result


def _quests(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result = []
    seen = set()
    for item in value[:_MAX_ITEMS]:
        if not isinstance(item, dict):
            continue
        identifier = _text(item.get("id"), 120).casefold().replace(" ", "-")
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)
        kind = _text(item.get("kind") or "side", 20).casefold()
        status = _text(item.get("status") or "active", 20).casefold()
        result.append(
            {
                "id": identifier,
                "kind": kind if kind in {"main", "side"} else "side",
                "status": status if status in {"active", "completed", "failed", "paused"} else "active",
                "objective": _text(item.get("objective")),
                "progress_current": _integer(item.get("progress_current"), 0, 100000),
                "progress_target": _integer(item.get("progress_target"), 0, 100000),
                "reward": _text(item.get("reward"), 500),
            }
        )
    return result


def _foreshadowing(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result = []
    seen = set()
    for item in value[:_MAX_ITEMS]:
        if not isinstance(item, dict):
            continue
        identifier = _text(item.get("id"), 120).casefold().replace(" ", "-")
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)
        status = _text(item.get("status") or "planted", 20).casefold()
        result.append(
            {
                "id": identifier,
                "status": status if status in {"planted", "developing", "resolved", "abandoned"} else "planted",
                "seed": _text(item.get("seed")),
                "payoff": _text(item.get("payoff")),
            }
        )
    return result


def _actor(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for collection in ("inventory", "skills", "conditions"):
        for operation in ("add", "remove"):
            key = f"{collection}_{operation}"
            values = _modifier_list(value.get(key))
            if values:
                result[key] = values
    return result


def parse_simulation_payload(raw: str) -> tuple[dict[str, Any], bool]:
    try:
        decoded = json.loads(unfence_json(raw))
    except (TypeError, json.JSONDecodeError):
        return {}, False
    if not isinstance(decoded, dict):
        return {}, False
    simulation = decoded.get("simulation")
    if simulation is None:
        return {}, True
    if not isinstance(simulation, dict):
        return {}, False
    on_screen = simulation.get("on_screen_npcs")
    return (
        {
            "relationships": _relationships(simulation.get("relationships")),
            "agendas": _agendas(simulation.get("agendas")),
            "on_screen_npcs": [
                name
                for item in (on_screen[:_MAX_ITEMS] if isinstance(on_screen, list) else [])
                if (name := _text(item, _MAX_NAME))
            ],
            "actor": _actor(simulation.get("actor")),
            "factions": _factions(simulation.get("factions")),
            "quests": _quests(simulation.get("quests")),
            "foreshadowing": _foreshadowing(simulation.get("foreshadowing")),
        },
        True,
    )


def merge_simulation_payload(previous: Any, current: Any) -> dict[str, Any]:
    base = dict(previous) if isinstance(previous, dict) else {}
    incoming = dict(current) if isinstance(current, dict) else {}
    result: dict[str, Any] = {}
    for key in ("relationships", "agendas", "factions", "quests", "foreshadowing"):
        left = base.get(key) if isinstance(base.get(key), list) else []
        right = incoming.get(key) if isinstance(incoming.get(key), list) else []
        result[key] = list(left) + list(right)
    names = []
    seen = set()
    for item in list(base.get("on_screen_npcs") or []) + list(incoming.get("on_screen_npcs") or []):
        value = _text(item, _MAX_NAME)
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            names.append(value)
    result["on_screen_npcs"] = names[:_MAX_ITEMS]
    actor: dict[str, Any] = {}
    for key in (
        "inventory_add",
        "inventory_remove",
        "skills_add",
        "skills_remove",
        "conditions_add",
        "conditions_remove",
    ):
        values = list((base.get("actor") or {}).get(key) or []) + list((incoming.get("actor") or {}).get(key) or [])
        if values:
            actor[key] = values[:_MAX_ITEMS]
    result["actor"] = actor
    return result
