"""Read-only player-facing projections of canonical simulation state."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from bridge.actor_visibility import npc_field_visible
from bridge.card_content import card_fields_from_file
from bridge.meta_repository import load_meta_value
from bridge.npc_repository import list_npc_entities, load_npc_fields, load_npc_fields_as_of
from bridge.session_repository import load_session_row
from bridge.settings import AppSettings
from bridge.simulation_context import context_cutoff
from bridge.simulation_narrative import canonical_plot_context
from bridge.simulation_repository import (
    list_checks,
    load_states_as_of,
    source_identity,
    tracker_progress,
)
from bridge.simulation_values import integer, key, modifier_entry, relationship_tier, text

_COLLECTIONS = ("inventory", "skills", "conditions")
_SECTIONS = ("relationships", "agendas", *_COLLECTIONS, "factions", "quests", "tasks", "checks")


@contextmanager
def tracker_read_snapshot(db: sqlite3.Connection) -> Iterator[None]:
    """Keep active scope, source boundary and visibility in one read transaction."""
    if db.in_transaction:
        yield
        return
    db.execute("BEGIN")
    try:
        yield
    finally:
        db.rollback()


def active_tracker_view(db: sqlite3.Connection, chat_id: str, *, app_settings: AppSettings) -> dict[str, Any]:
    """Resolve the server-owned active session without creating or normalizing it."""
    with tracker_read_snapshot(db):
        session_id = load_meta_value(db, f"active_session:{chat_id}", "default")
        session = load_session_row(db, chat_id, session_id)
        if session is None:
            return empty_tracker_view() | {"session": None}
        try:
            character = card_fields_from_file(session["character_file"], app_settings=app_settings).get("name", "")
        except (OSError, ValueError):
            character = ""
        return tracker_view(db, chat_id, session_id, active_character=character) | {
            "session": {"session_id": session_id, "title": session["title"]},
        }


def empty_tracker_view() -> dict[str, Any]:
    return {name: [] for name in _SECTIONS} | {
        "last_source_rowid": 0,
        "last_updated_at": None,
        "pending": False,
    }


def _npc_visible(
    db: sqlite3.Connection,
    entities: dict,
    cache: dict,
    kind: str,
    name: str,
    value: dict,
    cutoff: int,
    active_character: str,
) -> bool:
    matches = entities.get(key(name), [])
    fallback = kind == "relationship" or key(active_character) == key(name)
    if not matches:
        return fallback and value.get("_projection") is None
    if len(matches) != 1 or matches[0].first_seen_rowid > cutoff:
        return False
    entity = matches[0]
    if entity.npc_id not in cache:
        cache[entity.npc_id] = (load_npc_fields_as_of(db, entity.npc_id, cutoff), load_npc_fields(db, entity.npc_id))
    historical, current = (states.get(kind) for states in cache[entity.npc_id])
    # A later audience change cannot reveal an older secret or keep a revoked one visible.
    if any(state is not None and not npc_field_visible(state, active_character) for state in (historical, current)):
        return False
    if historical is None:
        return fallback and value.get("_projection") is None
    return current is not None


def _named(value: dict, name: str, *fields: str) -> dict[str, Any]:
    return {"name": text(value.get("display_name") or name, 160)} | {field: text(value.get(field)) for field in fields}


def _project(kind: str, name: str, value: dict) -> tuple[str, dict[str, Any]] | None:
    if kind == "relationship":
        bond = integer(value.get("bond"), -5, 20)
        return "relationships", _named(value, name) | {
            "bond": bond,
            "tier": relationship_tier(bond),
            "sparks": integer(value.get("sparks"), 0, 99),
            "grudge": integer(value.get("grudge"), 0, 99),
        }
    if kind == "agenda":
        return "agendas", _named(value, name, "objective", "status", "location") | {
            "step": integer(value.get("step"), 0, 20),
            "max_steps": integer(value.get("max_steps"), 1, 20, 1),
        }
    if kind == "faction":
        # Intel, lies and relations belong to private narrator context.
        return "factions", _named(value, name, "goal", "morale", "conflict")
    if kind == "task":
        return "tasks", _named(value, name, "objective", "stage", "status", "consequence") | {
            "progress_current": integer(value.get("progress_current"), 0, 20),
            "progress_target": integer(value.get("progress_target"), 0, 20),
            **{
                field: [text(item, 240) for item in value.get(field, [])[:16]]
                for field in ("completed_steps", "pending_steps", "complications")
            },
        }
    if kind == "quest":
        return "quests", _named(value, name, "kind", "status", "objective", "reward") | {
            "progress_current": integer(value.get("progress_current"), 0, 100000),
            "progress_target": integer(value.get("progress_target"), 0, 100000),
            "narrative_linked": bool(value.get("arc_id") or value.get("thread_id")),
        }
    # Foreshadowing seeds and payoffs have no player-visible audience in canonical state.
    return None


def tracker_view(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    *,
    active_character: str = "",
    through_rowid: int | None = None,
) -> dict[str, Any]:
    """Read a saved source prefix without extraction, dice, ticks or state repair."""
    with tracker_read_snapshot(db):
        cutoff = context_cutoff(db, chat_id, session_id, through_rowid)
        result = empty_tracker_view() | tracker_progress(db, chat_id, session_id, cutoff)
        entities: dict[str, list] = {}
        cache: dict = {}
        for entity in list_npc_entities(db, chat_id, session_id):
            for alias in {entity.canonical_name, *entity.aliases}:
                entities.setdefault(key(alias), []).append(entity)
        for kind, name, value, _source in load_states_as_of(db, chat_id, session_id, through_rowid=cutoff):
            if kind in {"relationship", "agenda"} and not _npc_visible(
                db,
                entities,
                cache,
                kind,
                name,
                value,
                cutoff,
                active_character,
            ):
                continue
            if kind == "actor":
                collection = value.get("collection")
                if collection in _COLLECTIONS and name == "user:" + collection:
                    result[collection] = [
                        entry for raw in value.get("entries", [])[:64] if (entry := modifier_entry(raw)) is not None
                    ]
                continue
            if kind == "quest":
                value = canonical_plot_context(db, chat_id, session_id, value, cutoff)
            projected = _project(kind, name, value)
            if projected:
                section, item = projected
                result[section].append(item)
        for check in list_checks(db, chat_id, session_id, through_rowid=cutoff, limit=8):
            try:
                _, digest = source_identity(db, chat_id, session_id, check["source_rowid"])
            except ValueError:
                continue
            if digest == check["source_digest"]:
                result["checks"].append(
                    {
                        field: check[field]
                        for field in ("domain", "actor", "action", "dc", "roll", "modifier", "delta", "outcome")
                    }
                )
        return result
