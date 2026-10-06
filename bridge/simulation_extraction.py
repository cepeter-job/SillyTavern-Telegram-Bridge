"""Bounded typed extraction and chronological accumulation of simulation changes."""

from __future__ import annotations

import json
from typing import Any

from bridge.json_fences import unfence_json
from bridge.simulation_repository import MAX_RECORD_BYTES
from bridge.simulation_values import MAX_ITEMS, MAX_NAME, boolean, integer, key, modifier_entry, text

_IDENTIFIERS = {"relationships": "npc", "agendas": "npc", "factions": "name", "quests": "id", "foreshadowing": "id"}
_ACTOR_COLLECTIONS = ("inventory", "skills", "conditions")


def _items(value: Any, limit: int) -> list:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError("Simulation collection exceeds its bound")
    return value


def _bounded(value: Any, limit: int = MAX_RECORD_BYTES) -> None:
    if len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()) > limit:
        raise ValueError("Simulation value exceeds its encoded storage bound")


def _relationship(item: dict) -> dict:
    if integer(item.get("bond_delta"), -2, 20) > 0:
        raise ValueError("positive BOND changes must come from Sparks conversion")
    result = {
        "npc": text(item.get("npc"), MAX_NAME),
        "bond_delta": integer(item.get("bond_delta"), -2, 0),
        "sparks_delta": integer(item.get("sparks_delta"), -2, 2),
        "grudge_delta": integer(item.get("grudge_delta"), -1, 1),
        "apology": boolean(item.get("apology")),
    }
    if "baseline" in item:
        baseline = item["baseline"]
        if not isinstance(baseline, dict):
            raise ValueError("Legacy relationship baseline must be an object")
        for name, low, high in (("bond", -5, 20), ("sparks", 0, 99), ("grudge", 0, 99)):
            if type(baseline.get(name)) is not int or not low <= baseline[name] <= high:
                raise ValueError("Legacy relationship baseline must contain exact bounded integers")
        quote = text(baseline.get("quote"), 500)
        if not quote:
            raise ValueError("Legacy relationship baseline requires a source quote")
        result["baseline"] = {name: baseline[name] for name in ("bond", "sparks", "grudge")} | {"quote": quote}
    return result


def _record(group: str, item: dict) -> dict:
    identifier = _IDENTIFIERS[group]
    name = text(item.get(identifier), MAX_NAME if identifier != "id" else 100)
    if identifier == "id":
        name = name.casefold().replace(" ", "-")
    result: dict[str, Any] = {identifier: name}
    fields = {
        "agendas": {"objective": 500, "location": 300, "status": 20},
        "factions": {"goal": 1000, "intel": 1000, "morale": 120, "conflict": 1000},
        "quests": {"kind": 20, "status": 20, "objective": 1000, "reward": 500, "arc_id": 100},
        "foreshadowing": {"status": 20, "seed": 1000, "payoff": 1000, "thread_id": 100, "arc_id": 100},
    }[group]
    for field, maximum in fields.items():
        if field in item:
            result[field] = text(item[field], maximum)
    if group == "agendas":
        for field, maximum, default in (("step", 20, 0), ("max_steps", 20, 1)):
            if field in item:
                result[field] = integer(item[field], default, maximum, default)
        if "complete" in item:
            result["complete"] = boolean(item["complete"])
        if "status" in result and result["status"] not in {"active", "paused", "completed"}:
            raise ValueError("Invalid agenda status")
    if group == "quests":
        for field in ("progress_current", "progress_target"):
            if field in item:
                result[field] = integer(item[field], 0, 100000)
        if "status" in result and result["status"] not in {"active", "completed", "failed", "paused"}:
            raise ValueError("Invalid quest status")
        if "kind" in result and result["kind"] not in {"main", "side"}:
            raise ValueError("Invalid quest kind")
    if (
        group == "foreshadowing"
        and "status" in result
        and result["status"] not in {"planted", "developing", "resolved", "abandoned"}
    ):
        raise ValueError("Invalid foreshadowing status")
    if group == "factions":
        if "lies" in item:
            result["lies"] = [text(value, 240) for value in _items(item["lies"], 32) if text(value, 240)]
        if "relations" in item:
            values = item["relations"]
            if not isinstance(values, dict) or len(values) > 32:
                raise ValueError("Faction relations must be a bounded object")
            result["relations"] = {text(k, 120): text(v, 240) for k, v in values.items() if text(k, 120)}
    _bounded(result)
    return result


def normalize_simulation_payload(value: Any, *, limit: int = MAX_ITEMS) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Simulation payload must be an object")
    result: dict[str, Any] = {}
    for group, identifier in _IDENTIFIERS.items():
        records = []
        for item in _items(value.get(group), limit):
            if not isinstance(item, dict) or not text(item.get(identifier), MAX_NAME):
                continue
            records.append(_relationship(item) if group == "relationships" else _record(group, item))
        result[group] = records
    result["on_screen_npcs"] = [
        name for item in _items(value.get("on_screen_npcs"), limit) if (name := text(item, MAX_NAME))
    ]
    actor = value.get("actor") or {}
    if not isinstance(actor, dict):
        raise ValueError("Actor updates must be an object")
    result["actor"] = {}
    for collection in _ACTOR_COLLECTIONS:
        sets = []
        for operation in ("remove", "add"):
            field = f"{collection}_{operation}"
            entries = [entry for item in _items(actor.get(field), limit) if (entry := modifier_entry(item))]
            sets.append({key(entry["name"]) for entry in entries})
            if entries:
                result["actor"][field] = entries
        if sets[0] & sets[1]:
            raise ValueError("One source part cannot both add and remove the same actor item")
    result = merge_simulation_payload({}, result)
    result["relationships"] = [_relationship(item) for item in result["relationships"]]
    _bounded(result, 262144)
    return result


def parse_simulation_payload(raw: str) -> tuple[dict[str, Any], bool]:
    try:
        decoded = json.loads(unfence_json(raw))
        if not isinstance(decoded, dict):
            return {}, False
        if "simulation" not in decoded:
            return {}, True
        return normalize_simulation_payload(decoded["simulation"], limit=32), True
    except (TypeError, ValueError, OverflowError):
        return {}, False


def merge_simulation_payload(previous: Any, current: Any) -> dict[str, Any]:
    base = previous if isinstance(previous, dict) else {}
    incoming = current if isinstance(current, dict) else {}
    result: dict[str, Any] = {}
    for group, identifier in _IDENTIFIERS.items():
        items: dict[str, dict] = {}
        for part in (base, incoming):
            for item in _items(part.get(group), MAX_ITEMS):
                if not isinstance(item, dict) or not (name := key(item.get(identifier))):
                    continue
                if group == "relationships" and name in items:
                    old = items[name]
                    merged = old | item
                    for field in ("bond_delta", "sparks_delta", "grudge_delta"):
                        merged[field] = integer(old.get(field), -128, 128) + integer(item.get(field), -128, 128)
                    merged["apology"] = boolean(old.get("apology")) or boolean(item.get("apology"))
                    items[name] = merged
                else:
                    items[name] = items.get(name, {}) | item
                if len(items) > MAX_ITEMS:
                    raise ValueError("Simulation source accumulation exceeds entity limit")
        result[group] = list(items.values())
    names = {}
    for part in (base, incoming):
        for item in _items(part.get("on_screen_npcs"), MAX_ITEMS):
            if name := text(item, MAX_NAME):
                names[key(name)] = name
    if len(names) > MAX_ITEMS:
        raise ValueError("Simulation participant accumulation exceeds limit")
    result["on_screen_npcs"] = list(names.values())
    actor: dict[str, list] = {}
    for collection in _ACTOR_COLLECTIONS:
        operations = {}
        for part in (base, incoming):
            current_actor = part.get("actor") or {}
            if not isinstance(current_actor, dict):
                raise ValueError("Actor updates must be an object")
            for operation in ("remove", "add"):
                for entry in _items(current_actor.get(f"{collection}_{operation}"), MAX_ITEMS):
                    name = key(entry.get("name") if isinstance(entry, dict) else entry)
                    if name:
                        operations[name] = (operation, entry)
            if len(operations) > MAX_ITEMS:
                raise ValueError("Actor source accumulation exceeds limit")
        for operation, entry in operations.values():
            actor.setdefault(f"{collection}_{operation}", []).append(entry)
    result["actor"] = actor
    _bounded(result, 262144)
    return result
