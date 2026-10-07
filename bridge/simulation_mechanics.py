"""Bounded relationship, agenda and actor mechanics for a single committed source row."""

from __future__ import annotations

import sqlite3
from typing import Any

from bridge.simulation_repository import list_states, load_state, store_state
from bridge.simulation_values import integer as _int
from bridge.simulation_values import key as _key
from bridge.simulation_values import merge_named_entries as _merge_named_entries
from bridge.simulation_values import text as _text

_RELATIONSHIP_KIND = "relationship"
_AGENDA_KIND = "agenda"
_ACTOR_KIND = "actor"


def _assistant_turn_index(db: sqlite3.Connection, chat_id: str, session_id: str, source_rowid: int) -> int:
    row = db.execute(
        "SELECT COUNT(*) FROM messages WHERE chat_id=? AND session_id=? AND role='assistant' AND id<=?",
        (chat_id, session_id, int(source_rowid)),
    ).fetchone()
    return int(row[0]) if row else 0


class SimulationMechanics:
    def _store(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session_id: str,
        kind: str,
        key: str,
        value: dict[str, Any],
        source_rowid: int,
        now: float,
    ) -> bool:
        return store_state(
            db,
            chat_id,
            session_id,
            kind,
            key,
            value,
            source_rowid=int(source_rowid),
            now=now,
        )

    def _relationship_updates(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session_id: str,
        payload: dict[str, Any],
        source_rowid: int,
        now: float,
        *,
        tick: bool = True,
    ) -> None:
        updates: dict[str, dict[str, Any]] = {}
        display: dict[str, str] = {}
        positive: set[str] = set()
        raw_updates = payload.get("relationships")
        if raw_updates is None:
            raw_updates = []
        if not isinstance(raw_updates, list) or len(raw_updates) > 64:
            raise ValueError("relationships must be a bounded list")
        for item in raw_updates:
            if not isinstance(item, dict):
                continue
            name = _text(item.get("npc"), 160)
            key = _key(name)
            if not key:
                continue
            current_row = load_state(db, chat_id, session_id, _RELATIONSHIP_KIND, key)
            current: dict[str, Any] = (
                dict(updates[key])
                if key in updates
                else dict(current_row[0])
                if current_row
                else {
                    "display_name": name,
                    "bond": 0,
                    "sparks": 0,
                    "grudge": 0,
                }
            )
            bond_delta = _int(item.get("bond_delta"), -2, 20)
            if bond_delta > 0:
                raise ValueError("positive BOND changes must come from Sparks conversion")
            sparks_delta = _int(item.get("sparks_delta"), -2, 2)
            grudge_delta = _int(item.get("grudge_delta"), -1, 1)
            if sparks_delta > 0:
                positive.add(key)
            current["display_name"] = name or current.get("display_name") or key
            current["bond"] = _int(current.get("bond"), -5, 20) + bond_delta
            current["bond"] = _int(current["bond"], -5, 20)
            current["sparks"] = max(0, _int(current.get("sparks"), 0, 99) + sparks_delta)
            current["grudge"] = max(0, _int(current.get("grudge"), 0, 99) + grudge_delta)
            if item.get("apology") is True:
                current["grudge"] = 0
            updates[key] = current
            display[key] = current["display_name"]

        turn = _assistant_turn_index(db, chat_id, session_id, source_rowid) if tick else 0
        current_relationships = {
            key: dict(value)
            for _kind, key, value, _rowid, _updated in list_states(db, chat_id, session_id, kind=_RELATIONSHIP_KIND)
        }
        current_relationships.update(updates)
        for key, current in current_relationships.items():
            bond = _int(current.get("bond"), -5, 20)
            sparks = _int(current.get("sparks"), 0, 99)
            grudge = _int(current.get("grudge"), 0, 99)
            if turn and turn % 3 == 0:
                if grudge >= 5:
                    bond = max(-5, bond - 1)
                    grudge = 0
                elif grudge > 0:
                    grudge -= 1
            if turn and turn % 5 == 0:
                threshold = 14 if grudge >= 3 else 7
                if sparks >= threshold:
                    bond = min(20, bond + 1)
                    sparks = 0
                elif key not in positive and sparks > 0:
                    sparks -= 1
            final: dict[str, Any] = {
                "display_name": _text(current.get("display_name") or display.get(key) or key, 160),
                "bond": bond,
                "sparks": sparks,
                "grudge": grudge,
            }
            if "_projection" in current:
                final["_projection"] = current["_projection"]
            self._store(db, chat_id, session_id, _RELATIONSHIP_KIND, key, final, source_rowid, now)

    def _agenda_updates(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session_id: str,
        payload: dict[str, Any],
        source_rowid: int,
        now: float,
        *,
        tick: bool = True,
    ) -> None:
        on_screen_raw = payload.get("on_screen_npcs") or []
        if not isinstance(on_screen_raw, list) or len(on_screen_raw) > 64:
            raise ValueError("on_screen_npcs must be a bounded list")
        on_screen = {_key(item) for item in on_screen_raw if _key(item)}
        explicit: set[str] = set()
        raw_agendas = payload.get("agendas") or []
        if not isinstance(raw_agendas, list) or len(raw_agendas) > 64:
            raise ValueError("agendas must be a bounded list")
        for item in raw_agendas:
            if not isinstance(item, dict):
                continue
            name = _text(item.get("npc"), 160)
            key = _key(name)
            if not key:
                continue
            current_row = load_state(db, chat_id, session_id, _AGENDA_KIND, key)
            current = dict(current_row[0]) if current_row else {}
            objective = _text(item.get("objective", current.get("objective")), 500)
            if not objective:
                continue
            max_steps = _int(item.get("max_steps", current.get("max_steps", 1)), 1, 20, 1)
            step = _int(item.get("step", current.get("step", 0)), 0, max_steps)
            status = _key(item.get("status") or current.get("status") or "active")
            if status not in {"active", "completed", "paused"}:
                status = "active"
            if item.get("complete") is True or step >= max_steps:
                step = max_steps
                status = "completed"
            state = {
                "display_name": name,
                "objective": objective,
                "step": step,
                "max_steps": max_steps,
                "location": _text(item.get("location") or current.get("location") or "", 300),
                "status": status,
            }
            if "_projection" in current:
                state["_projection"] = current["_projection"]
            explicit.add(key)
            self._store(db, chat_id, session_id, _AGENDA_KIND, key, state, source_rowid, now)

        for _kind, key, current, _rowid, _updated in list_states(db, chat_id, session_id, kind=_AGENDA_KIND):
            if not tick or key in explicit or key in on_screen or current.get("status") != "active":
                continue
            max_steps = _int(current.get("max_steps"), 1, 20, 1)
            step = min(max_steps, _int(current.get("step"), 0, max_steps) + 1)
            updated = dict(current)
            updated["step"] = step
            if step >= max_steps:
                updated["status"] = "completed"
            self._store(db, chat_id, session_id, _AGENDA_KIND, key, updated, source_rowid, now)

    def _actor_update(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session_id: str,
        payload: dict[str, Any],
        source_rowid: int,
        now: float,
    ) -> None:
        raw = payload.get("actor")
        if not raw:
            return
        for name in ("inventory", "skills", "conditions"):
            if not any(f"{name}_{op}" in raw for op in ("add", "remove")):
                continue
            record_key = f"user:{name}"
            current = load_state(db, chat_id, session_id, _ACTOR_KIND, record_key)
            entries = current[0].get("entries", []) if current else []
            updated = {
                "collection": name,
                "entries": _merge_named_entries(entries, raw.get(f"{name}_add"), raw.get(f"{name}_remove")),
            }
            self._store(db, chat_id, session_id, _ACTOR_KIND, record_key, updated, source_rowid, now)

    def _replace_named_states(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session_id: str,
        payload: dict[str, Any],
        source_rowid: int,
        now: float,
        *,
        payload_key: str,
        kind: str,
        identifier: str,
        fields: tuple[str, ...],
    ) -> None:
        raw = payload.get(payload_key) or []
        if not isinstance(raw, list) or len(raw) > 64:
            raise ValueError(f"{payload_key} must be a bounded list")
        for item in raw:
            if not isinstance(item, dict):
                continue
            display = _text(item.get(identifier), 160)
            key = _key(display)
            if not key:
                continue
            current = load_state(db, chat_id, session_id, kind, key)
            value: dict[str, Any] = dict(current[0]) if current else {"display_name": display}
            for field in fields:
                if field not in item:
                    continue
                raw_value = item.get(field)
                if field in {"progress_current", "progress_target"}:
                    value[field] = _int(raw_value, 0, 100000)
                elif field == "relations":
                    if isinstance(raw_value, dict):
                        value[field] = {
                            _text(k, 120): _text(v, 240) for k, v in list(raw_value.items())[:32] if _text(k, 120)
                        }
                    else:
                        value[field] = {}
                elif field in {"lies", "completed_steps", "pending_steps", "complications"}:
                    value[field] = (
                        [_text(v, 240) for v in raw_value[:32] if _text(v, 240)] if isinstance(raw_value, list) else []
                    )
                else:
                    value[field] = _text(raw_value, 1000)
            self._store(db, chat_id, session_id, kind, key, value, source_rowid, now)
