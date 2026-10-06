"""Guarded NPC field projections; native/manual ownership is never inferred from prose."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import asdict
from typing import Any

from bridge.npc_repository import find_npc_by_name_or_alias, list_npc_entities, load_npc_fields
from bridge.npc_service import NpcService
from bridge.npc_types import NpcExtractionGroup, NpcOperation
from bridge.repository_contracts import NpcFieldState
from bridge.simulation_extraction import normalize_simulation_payload
from bridge.simulation_repository import list_states, load_state, store_state
from bridge.simulation_values import key, relationship_tier


def canonicalize_tracker_npcs(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    payload: dict[str, Any],
    *,
    source_rowid: int,
    now: float,
    primary_name: str = "",
    user_name: str = "",
) -> dict[str, Any]:
    blocked = {key(primary_name), key(user_name)} - {""}
    names = {}
    ambiguous = set()
    for entity in list_npc_entities(db, chat_id, session_id):
        if entity.first_seen_rowid > source_rowid:
            continue
        aliases = {key(entity.canonical_name), *(key(alias) for alias in entity.aliases)}
        for alias in aliases:
            if alias in names:
                ambiguous.add(alias)
            names[alias] = entity.display_name
        if aliases & blocked:
            blocked.update(aliases)
    eligible = {alias: name for alias, name in names.items() if alias not in blocked | ambiguous}
    _adopt_tracker_aliases(db, chat_id, session_id, eligible, source_rowid, now)
    result = dict(payload)
    for group in ("relationships", "agendas"):
        result[group] = [
            dict(item, npc=names.get(key(item["npc"]), item["npc"]))
            for item in payload.get(group, [])
            if key(item["npc"]) not in blocked | ambiguous
        ]
    result["on_screen_npcs"] = [names.get(key(name), name) for name in payload.get("on_screen_npcs", [])]
    return normalize_simulation_payload(result)


def _adopt_tracker_aliases(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    names: dict[str, str],
    source_rowid: int,
    now: float,
) -> None:
    for kind in ("relationship", "agenda"):
        groups: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        for _, name, value, _, _ in list_states(db, chat_id, session_id, kind=kind):
            if name in names:
                groups.setdefault(key(names[name]), []).append((name, value))
        for canonical, records in groups.items():
            if all(name == canonical for name, _ in records):
                continue
            mechanics = [
                {k: v for k, v in value.items() if k not in {"display_name", "_projection"}} for _, value in records
            ]
            if any(value != mechanics[0] for value in mechanics[1:]):
                raise ValueError("Conflicting simulation alias states require explicit correction")
            adopted = dict(next((value for name, value in records if name == canonical), records[0][1]))
            adopted["display_name"] = names[records[0][0]]
            entity = find_npc_by_name_or_alias(db, chat_id, session_id, canonical)
            current = load_npc_fields(db, entity.npc_id).get(kind) if entity else None
            projections = [value["_projection"] for _, value in records if value.get("_projection") is not None]
            if projections:
                fingerprint = _fingerprint(current)
                adopted["_projection"] = fingerprint if fingerprint in projections else projections[0]
            # Attribute identity changes to this source; retain old keys in historical reads.
            # Delete first so an otherwise full domain can still rename one live entity.
            for name, _ in records:
                if name != canonical:
                    store_state(db, chat_id, session_id, kind, name, None, source_rowid=source_rowid, now=now)
            store_state(db, chat_id, session_id, kind, canonical, adopted, source_rowid=source_rowid, now=now)


def _fingerprint(field: NpcFieldState | None) -> dict[str, Any] | None:
    if field is None:
        return None
    value = asdict(field)
    value.pop("npc_id")
    value["known_by"] = list(value["known_by"])
    return value


def is_managed_field(db: sqlite3.Connection, chat_id: str, session_id: str, name: str, field_key: str) -> bool:
    if field_key not in {"relationship", "agenda"}:
        return False
    entity = find_npc_by_name_or_alias(db, chat_id, session_id, name)
    if entity is None:
        return False
    state = load_state(db, chat_id, session_id, field_key, key(entity.canonical_name))
    current = load_npc_fields(db, entity.npc_id).get(field_key)
    return bool(state and current and state[0].get("_projection") == _fingerprint(current))


def project_simulation_state(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    source_rowid: int,
    *,
    primary_name: str = "",
    user_name: str = "",
) -> None:
    for kind in ("relationship", "agenda"):
        for _, name, value, _, _ in list_states(db, chat_id, session_id, kind=kind):
            entity = find_npc_by_name_or_alias(db, chat_id, session_id, name)
            if (
                entity is None
                or entity.first_seen_rowid > source_rowid
                or key(entity.canonical_name) in {key(primary_name), key(user_name)}
            ):
                continue
            current = load_npc_fields(db, entity.npc_id).get(kind)
            previous = value.get("_projection")
            if current is not None:
                if current.field_mode != "mutable" or previous != _fingerprint(current):
                    continue
            elif previous is not None:
                # A native clear/undo relinquishes ownership, even when no field remains.
                continue
            if kind == "relationship":
                rendered = f"Toward the user: {relationship_tier(value.get('bond', 0))}."
            else:
                rendered = (
                    f"{value.get('objective', '')} "
                    f"({value.get('step', 0)}/{value.get('max_steps', 1)}; {value.get('status', 'active')})."
                )
            visibility = current.visibility if current else ("shared" if kind == "relationship" else "restricted")
            known_by = current.known_by if current else (entity.display_name,)
            NpcService().apply_group(
                db,
                chat_id,
                session_id,
                NpcExtractionGroup(
                    entity.display_name, (), (NpcOperation(kind, "set", rendered, "mutable", visibility, known_by),)
                ),
                source_rowid=source_rowid,
                primary_name=primary_name,
                user_name=user_name,
            )
            after = load_npc_fields(db, entity.npc_id).get(kind)
            if after is not None and after.value == rendered:
                store_state(
                    db,
                    chat_id,
                    session_id,
                    kind,
                    name,
                    value | {"_projection": _fingerprint(after)},
                    source_rowid=source_rowid,
                    now=time.time(),
                )
