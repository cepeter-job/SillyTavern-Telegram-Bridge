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
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError("Simulation collection must be a bounded array")
    return value


def _typed_fields(item: dict, fields: Any, expected: type) -> None:
    if any(field in item and type(item[field]) is not expected for field in fields):
        raise ValueError("Simulation field has an invalid JSON type")


def _name(value: Any, maximum: int = MAX_NAME) -> str:
    if not isinstance(value, str) or not (name := text(value, maximum)):
        raise ValueError("Simulation identity must be nonempty text")
    return name


def _modifier(value: Any) -> dict[str, Any]:
    item = {"name": value} if isinstance(value, str) else value
    if not isinstance(item, dict):
        raise ValueError("Actor item must be text or an object")
    _name(item.get("name"), 120)
    _typed_fields(item, ("domain",), str)
    _typed_fields(item, ("modifier",), int)
    result = modifier_entry(item)
    if result is None:
        raise ValueError("Actor item must have a valid identity")
    return result


def _bounded(value: Any, limit: int = MAX_RECORD_BYTES) -> None:
    if len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()) > limit:
        raise ValueError("Simulation value exceeds its encoded storage bound")


def _relationship(item: dict) -> dict:
    _typed_fields(item, ("bond_delta", "sparks_delta", "grudge_delta"), int)
    _typed_fields(item, ("apology",), bool)
    if integer(item.get("bond_delta"), -2, 20) > 0:
        raise ValueError("positive BOND changes must come from Sparks conversion")
    result = {
        "npc": _name(item.get("npc")),
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
        quote = _name(baseline.get("quote"), 500)
        result["baseline"] = {name: baseline[name] for name in ("bond", "sparks", "grudge")} | {"quote": quote}
    return result


def _record(group: str, item: dict) -> dict:
    identifier = _IDENTIFIERS[group]
    name = _name(item.get(identifier), MAX_NAME if identifier != "id" else 100)
    if identifier == "id":
        name = name.casefold().replace(" ", "-")
    result: dict[str, Any] = {identifier: name}
    fields = {
        "agendas": {"objective": 500, "location": 300, "status": 20},
        "factions": {"goal": 1000, "intel": 1000, "morale": 120, "conflict": 1000},
        "quests": {"kind": 20, "status": 20, "objective": 1000, "reward": 500, "arc_id": 100},
        "foreshadowing": {"status": 20, "seed": 1000, "payoff": 1000, "thread_id": 100, "arc_id": 100},
    }[group]
    _typed_fields(item, fields, str)
    for field, maximum in fields.items():
        if field in item:
            result[field] = text(item[field], maximum)
    if group == "agendas":
        _typed_fields(item, ("step", "max_steps"), int)
        _typed_fields(item, ("complete",), bool)
        for field, maximum, default in (("step", 20, 0), ("max_steps", 20, 1)):
            if field in item:
                result[field] = integer(item[field], default, maximum, default)
        if "complete" in item:
            result["complete"] = boolean(item["complete"])
        if "status" in result and result["status"] not in {"active", "paused", "completed"}:
            raise ValueError("Invalid agenda status")
    if group == "quests":
        _typed_fields(item, ("progress_current", "progress_target"), int)
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
            result["lies"] = [_name(value, 240) for value in _items(item["lies"], 32)]
        if "relations" in item:
            values = item["relations"]
            if not isinstance(values, dict) or len(values) > 32:
                raise ValueError("Faction relations must be a bounded object")
            _typed_fields(values, values, str)
            result["relations"] = {_name(k, 120): text(v, 240) for k, v in values.items()}
    _bounded(result)
    return result


def normalize_simulation_payload(value: Any, *, limit: int = MAX_ITEMS) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Simulation payload must be an object")
    result: dict[str, Any] = {}
    for group in _IDENTIFIERS:
        records = []
        for item in _items(value.get(group, []), limit):
            if not isinstance(item, dict):
                raise ValueError("Simulation entity must be an object")
            records.append(_relationship(item) if group == "relationships" else _record(group, item))
        result[group] = records
    result["on_screen_npcs"] = [_name(item) for item in _items(value.get("on_screen_npcs", []), limit)]
    actor = value.get("actor", {})
    if not isinstance(actor, dict):
        raise ValueError("Actor updates must be an object")
    result["actor"] = {}
    for collection in _ACTOR_COLLECTIONS:
        sets = []
        for operation in ("remove", "add"):
            field = f"{collection}_{operation}"
            entries = [_modifier(item) for item in _items(actor.get(field, []), limit)]
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
            for item in _items(part.get(group, []), MAX_ITEMS):
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
        for item in _items(part.get("on_screen_npcs", []), MAX_ITEMS):
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
                for entry in _items(current_actor.get(f"{collection}_{operation}", []), MAX_ITEMS):
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
