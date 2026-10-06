"""Guarded NPC field projections; native/manual ownership is never inferred from prose."""

from __future__ import annotations

import time
from dataclasses import asdict

from bridge.npc_repository import find_npc_by_name_or_alias, list_npc_entities, load_npc_fields
from bridge.npc_service import NpcService
from bridge.npc_types import NpcExtractionGroup, NpcOperation
from bridge.simulation_extraction import normalize_simulation_payload
from bridge.simulation_repository import list_states, load_state, store_state
from bridge.simulation_values import key, relationship_tier


def canonicalize_tracker_npcs(db, chat_id, session_id, payload, *, primary_name="", user_name=""):
    blocked = {key(primary_name), key(user_name)} - {""}
    names = {}
    ambiguous = set()
    for entity in list_npc_entities(db, chat_id, session_id):
        aliases = {key(entity.canonical_name), *(key(alias) for alias in entity.aliases)}
        for alias in aliases:
            if alias in names:
                ambiguous.add(alias)
            names[alias] = entity.display_name
        if aliases & blocked:
            blocked.update(aliases)
    result = dict(payload)
    for group in ("relationships", "agendas"):
        result[group] = [
            dict(item, npc=names.get(key(item["npc"]), item["npc"]))
            for item in payload.get(group, [])
            if key(item["npc"]) not in blocked | ambiguous
        ]
    result["on_screen_npcs"] = [names.get(key(name), name) for name in payload.get("on_screen_npcs", [])]
    return normalize_simulation_payload(result)


def _fingerprint(field):
    if field is None:
        return None
    value = asdict(field)
    value.pop("npc_id")
    value["known_by"] = list(value["known_by"])
    return value


def is_managed_field(db, chat_id, session_id, name, field_key):
    if field_key not in {"relationship", "agenda"}:
        return False
    entity = find_npc_by_name_or_alias(db, chat_id, session_id, name)
    if entity is None:
        return False
    state = load_state(db, chat_id, session_id, field_key, key(entity.canonical_name))
    current = load_npc_fields(db, entity.npc_id).get(field_key)
    return bool(state and current and state[0].get("_projection") == _fingerprint(current))


def project_simulation_state(db, chat_id, session_id, source_rowid, *, primary_name="", user_name=""):
    for kind in ("relationship", "agenda"):
        for _, name, value, _, _ in list_states(db, chat_id, session_id, kind=kind):
            entity = find_npc_by_name_or_alias(db, chat_id, session_id, name)
            if entity is None or key(entity.canonical_name) in {key(primary_name), key(user_name)}:
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
            visibility = current.visibility if current else "restricted"
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
