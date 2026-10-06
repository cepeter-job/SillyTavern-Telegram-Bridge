"""Canonical tracker mechanics replacing prompt-maintained Internal States."""

from __future__ import annotations

import secrets
import sqlite3
import time
from typing import Any

from bridge.simulation_repository import (
    insert_check,
    list_checks,
    list_states,
    load_check,
    load_state,
    load_states_as_of,
    purge_session as purge_repository_session,
    rollback_from_row as rollback_repository_from_row,
    store_state,
)
from bridge.sqlite_store import write_transaction

_RELATIONSHIP_KIND = "relationship"
_AGENDA_KIND = "agenda"
_ACTOR_KIND = "actor"
_FACTION_KIND = "faction"
_QUEST_KIND = "quest"
_FORESHADOW_KIND = "foreshadowing"
_ALLOWED_KINDS = frozenset(
    {
        _RELATIONSHIP_KIND,
        _AGENDA_KIND,
        _ACTOR_KIND,
        _FACTION_KIND,
        _QUEST_KIND,
        _FORESHADOW_KIND,
    }
)
_MAX_STATE_ITEMS = 64
_MAX_TEXT = 1000
_MAX_CONTEXT = 6000


def _key(value: Any) -> str:
    return " ".join(str(value or "").split()).strip().casefold()


def _text(value: Any, maximum: int = _MAX_TEXT) -> str:
    return " ".join(str(value or "").split()).strip()[:maximum]


def _int(value: Any, low: int, high: int, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    return max(low, min(high, parsed))


def _assistant_turn_index(db: sqlite3.Connection, chat_id: str, session_id: str, source_rowid: int) -> int:
    row = db.execute(
        "SELECT COUNT(*) FROM messages WHERE chat_id=? AND session_id=? AND role='assistant' AND id<=?",
        (chat_id, session_id, int(source_rowid)),
    ).fetchone()
    return int(row[0]) if row else 0


def _tier(bond: int) -> str:
    if bond <= -3:
        return "hostile"
    if bond <= 2:
        return "neutral"
    if bond <= 7:
        return "warmth"
    if bond <= 15:
        return "trust"
    return "love"


def _normalize_modifier_entry(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        name = _text(value, 120)
        return {"name": name, "domain": "any", "modifier": 0} if name else None
    if not isinstance(value, dict):
        return None
    name = _text(value.get("name"), 120)
    domain = _key(value.get("domain") or "any")[:40]
    if not name or not domain:
        return None
    return {"name": name, "domain": domain, "modifier": _int(value.get("modifier"), -2, 2)}


def _merge_named_entries(current: list[dict[str, Any]], adds: Any, removes: Any) -> list[dict[str, Any]]:
    items = {_key(item.get("name")): dict(item) for item in current if isinstance(item, dict) and _key(item.get("name"))}
    if isinstance(removes, list):
        for item in removes[:_MAX_STATE_ITEMS]:
            name = _key(item.get("name") if isinstance(item, dict) else item)
            if name:
                items.pop(name, None)
    if isinstance(adds, list):
        for item in adds[:_MAX_STATE_ITEMS]:
            normalized = _normalize_modifier_entry(item)
            if normalized:
                items[_key(normalized["name"])] = normalized
    return list(items.values())[:_MAX_STATE_ITEMS]


class SimulationService:
    """Validate, apply, rewind and render canonical simulation trackers."""

    def state(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session_id: str,
        kind: str,
        entity_key: str,
        *,
        through_rowid: int | None = None,
    ) -> dict[str, Any] | None:
        kind = _key(kind)
        entity_key = _key(entity_key)
        if kind not in _ALLOWED_KINDS or not entity_key:
            return None
        if through_rowid is None:
            row = load_state(db, chat_id, session_id, kind, entity_key)
            return dict(row[0]) if row else None
        for item_kind, item_key, value, _rowid in load_states_as_of(
            db, chat_id, session_id, through_rowid=int(through_rowid)
        ):
            if item_kind == kind and item_key == entity_key:
                return dict(value)
        return None

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
    ) -> None:
        updates: dict[str, dict[str, Any]] = {}
        display: dict[str, str] = {}
        positive: set[str] = set()
        raw_updates = payload.get("relationships")
        if raw_updates is None:
            raw_updates = []
        if not isinstance(raw_updates, list) or len(raw_updates) > 32:
            raise ValueError("relationships must be a bounded list")
        for item in raw_updates:
            if not isinstance(item, dict):
                continue
            name = _text(item.get("npc"), 120)
            key = _key(name)
            if not key:
                continue
            current_row = load_state(db, chat_id, session_id, _RELATIONSHIP_KIND, key)
            current = dict(current_row[0]) if current_row else {
                "display_name": name,
                "bond": 0,
                "sparks": 0,
                "grudge": 0,
            }
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
            if bool(item.get("apology")):
                current["grudge"] = 0
            updates[key] = current
            display[key] = current["display_name"]

        turn = _assistant_turn_index(db, chat_id, session_id, source_rowid)
        current_relationships = {
            key: dict(value)
            for _kind, key, value, _rowid, _updated in list_states(
                db, chat_id, session_id, kind=_RELATIONSHIP_KIND
            )
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
            final = {
                "display_name": _text(current.get("display_name") or display.get(key) or key, 120),
                "bond": bond,
                "sparks": sparks,
                "grudge": grudge,
            }
            self._store(db, chat_id, session_id, _RELATIONSHIP_KIND, key, final, source_rowid, now)

    def _agenda_updates(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session_id: str,
        payload: dict[str, Any],
        source_rowid: int,
        now: float,
    ) -> None:
        on_screen_raw = payload.get("on_screen_npcs") or []
        if not isinstance(on_screen_raw, list) or len(on_screen_raw) > 64:
            raise ValueError("on_screen_npcs must be a bounded list")
        on_screen = {_key(item) for item in on_screen_raw if _key(item)}
        explicit: set[str] = set()
        raw_agendas = payload.get("agendas") or []
        if not isinstance(raw_agendas, list) or len(raw_agendas) > 32:
            raise ValueError("agendas must be a bounded list")
        for item in raw_agendas:
            if not isinstance(item, dict):
                continue
            name = _text(item.get("npc"), 120)
            key = _key(name)
            objective = _text(item.get("objective"), 500)
            if not key or not objective:
                continue
            current_row = load_state(db, chat_id, session_id, _AGENDA_KIND, key)
            current = dict(current_row[0]) if current_row else {}
            max_steps = _int(item.get("max_steps", current.get("max_steps", 1)), 1, 20, 1)
            step = _int(item.get("step", current.get("step", 0)), 0, max_steps)
            status = _key(item.get("status") or current.get("status") or "active")
            if status not in {"active", "completed", "paused"}:
                status = "active"
            if bool(item.get("complete")) or step >= max_steps:
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
            explicit.add(key)
            self._store(db, chat_id, session_id, _AGENDA_KIND, key, state, source_rowid, now)

        for _kind, key, current, _rowid, _updated in list_states(db, chat_id, session_id, kind=_AGENDA_KIND):
            if key in explicit or key in on_screen or current.get("status") != "active":
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
        if raw is None:
            return
        if not isinstance(raw, dict):
            raise ValueError("actor state must be an object")
        current_row = load_state(db, chat_id, session_id, _ACTOR_KIND, "user")
        current = dict(current_row[0]) if current_row else {"inventory": [], "skills": [], "conditions": []}
        for name in ("inventory", "skills", "conditions"):
            current[name] = _merge_named_entries(
                list(current.get(name) or []),
                raw.get(f"{name}_add"),
                raw.get(f"{name}_remove"),
            )
        self._store(db, chat_id, session_id, _ACTOR_KIND, "user", current, source_rowid, now)

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
        if not isinstance(raw, list) or len(raw) > 32:
            raise ValueError(f"{payload_key} must be a bounded list")
        for item in raw:
            if not isinstance(item, dict):
                continue
            display = _text(item.get(identifier), 160)
            key = _key(display)
            if not key:
                continue
            value: dict[str, Any] = {"display_name": display}
            for field in fields:
                raw_value = item.get(field)
                if field in {"progress_current", "progress_target"}:
                    value[field] = _int(raw_value, 0, 100000)
                elif field == "relations":
                    if isinstance(raw_value, dict):
                        value[field] = {
                            _text(k, 120): _text(v, 240)
                            for k, v in list(raw_value.items())[:32]
                            if _text(k, 120)
                        }
                    else:
                        value[field] = {}
                elif field == "lies":
                    value[field] = [_text(v, 240) for v in raw_value[:32] if _text(v, 240)] if isinstance(raw_value, list) else []
                else:
                    value[field] = _text(raw_value, 1000)
            self._store(db, chat_id, session_id, kind, key, value, source_rowid, now)

    def apply_payload(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session_id: str,
        payload: dict[str, Any],
        *,
        source_rowid: int,
    ) -> None:
        if not isinstance(payload, dict):
            raise ValueError("simulation payload must be an object")
        now = time.time()
        with write_transaction(db):
            self._relationship_updates(db, chat_id, session_id, payload, source_rowid, now)
            self._agenda_updates(db, chat_id, session_id, payload, source_rowid, now)
            self._actor_update(db, chat_id, session_id, payload, source_rowid, now)
            self._replace_named_states(
                db,
                chat_id,
                session_id,
                payload,
                source_rowid,
                now,
                payload_key="factions",
                kind=_FACTION_KIND,
                identifier="name",
                fields=("goal", "intel", "lies", "morale", "conflict", "relations"),
            )
            self._replace_named_states(
                db,
                chat_id,
                session_id,
                payload,
                source_rowid,
                now,
                payload_key="quests",
                kind=_QUEST_KIND,
                identifier="id",
                fields=("kind", "status", "objective", "progress_current", "progress_target", "reward"),
            )
            self._replace_named_states(
                db,
                chat_id,
                session_id,
                payload,
                source_rowid,
                now,
                payload_key="foreshadowing",
                kind=_FORESHADOW_KIND,
                identifier="id",
                fields=("status", "seed", "payoff"),
            )

    def rollback_from_row(self, db: sqlite3.Connection, chat_id: str, session_id: str, rowid: int) -> int:
        with write_transaction(db):
            return rollback_repository_from_row(db, chat_id, session_id, int(rowid), now=time.time())

    def purge_session(self, db: sqlite3.Connection, chat_id: str, session_id: str) -> None:
        with write_transaction(db):
            purge_repository_session(db, chat_id, session_id)

    def actor_modifier(self, db: sqlite3.Connection, chat_id: str, session_id: str, domain: str) -> int:
        row = load_state(db, chat_id, session_id, _ACTOR_KIND, "user")
        if row is None:
            return 0
        wanted = _key(domain)
        total = 0
        for collection in ("inventory", "skills", "conditions"):
            for item in row[0].get(collection, []):
                if not isinstance(item, dict):
                    continue
                item_domain = _key(item.get("domain") or "any")
                if item_domain in {"any", wanted}:
                    total += _int(item.get("modifier"), -2, 2)
        return _int(total, -6, 6)

    @staticmethod
    def classify_check(roll: int, dc: int, modifier: int) -> str:
        roll = _int(roll, 1, 20, 1)
        dc = _int(dc, 1, 20, 1)
        modifier = _int(modifier, -20, 20)
        delta = roll + modifier - dc
        if roll == 1:
            return "critical_failure"
        if roll == 20:
            return "critical_success"
        if delta >= 8:
            return "critical_success"
        if delta <= -8:
            return "critical_failure"
        if delta >= 0:
            return "success"
        if delta >= -3:
            return "near_miss"
        return "failure"

    def perform_check(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session_id: str,
        *,
        request_key: str,
        domain: str,
        actor: str,
        action: str,
        dc: int,
        roll: int | None = None,
        modifier: int | None = None,
        source_rowid: int | None = None,
    ) -> dict[str, Any]:
        request_key = _text(request_key, 160)
        if not request_key:
            raise ValueError("check request key is required")
        existing = load_check(db, chat_id, session_id, request_key)
        if existing is not None:
            return existing
        domain = _key(domain)[:40]
        actor = _text(actor, 120)
        action = _text(action, 500)
        dc = _int(dc, 1, 20, 1)
        if not domain or not actor or not action:
            raise ValueError("check domain, actor and action are required")
        final_roll = _int(roll if roll is not None else secrets.randbelow(20) + 1, 1, 20, 1)
        final_modifier = (
            self.actor_modifier(db, chat_id, session_id, domain)
            if modifier is None
            else _int(modifier, -20, 20)
        )
        if source_rowid is None:
            row = db.execute(
                "SELECT COALESCE(MAX(id),0) FROM messages WHERE chat_id=? AND session_id=?",
                (chat_id, session_id),
            ).fetchone()
            source_rowid = int(row[0]) if row else 0
        result = {
            "request_key": request_key,
            "source_rowid": int(source_rowid),
            "domain": domain,
            "actor": actor,
            "action": action,
            "dc": dc,
            "roll": final_roll,
            "modifier": final_modifier,
            "delta": final_roll + final_modifier - dc,
            "outcome": self.classify_check(final_roll, dc, final_modifier),
            "created_at": time.time(),
        }
        with write_transaction(db):
            existing = load_check(db, chat_id, session_id, request_key)
            if existing is not None:
                return existing
            insert_check(db, chat_id, session_id, result)
        return result

    def context_for_prompt(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session_id: str,
        *,
        through_rowid: int | None = None,
    ) -> str:
        if through_rowid is None:
            states = [(kind, key, value, rowid) for kind, key, value, rowid, _ in list_states(db, chat_id, session_id)]
        else:
            states = load_states_as_of(db, chat_id, session_id, through_rowid=int(through_rowid))
        if not states and not list_checks(db, chat_id, session_id, through_rowid=through_rowid, limit=3):
            return ""
        lines = [
            "Bridge Simulation State (canonical; never print tracker blocks or mechanics unless explicitly asked):"
        ]
        for kind, key, value, _rowid in states:
            if kind == _RELATIONSHIP_KIND:
                bond = _int(value.get("bond"), -5, 20)
                lines.append(
                    f"- REL {value.get('display_name') or key}: bond={bond} tier={_tier(bond)} "
                    f"sparks={_int(value.get('sparks'),0,99)} grudge={_int(value.get('grudge'),0,99)}"
                )
            elif kind == _AGENDA_KIND:
                lines.append(
                    f"- AGENDA {value.get('display_name') or key}: {value.get('objective','')} "
                    f"[{value.get('step',0)}/{value.get('max_steps',1)} {value.get('status','active')}] "
                    f"@ {value.get('location','')}"
                )
            elif kind == _ACTOR_KIND:
                for label in ("inventory", "skills", "conditions"):
                    names = [item.get("name", "") for item in value.get(label, []) if isinstance(item, dict)]
                    if names:
                        lines.append(f"- USER {label}: " + ", ".join(names))
            elif kind == _FACTION_KIND:
                lines.append(
                    f"- FACTION {value.get('display_name') or key}: goal={value.get('goal','')} "
                    f"morale={value.get('morale','')} conflict={value.get('conflict','')}"
                )
            elif kind == _QUEST_KIND:
                lines.append(
                    f"- QUEST {value.get('display_name') or key}: {value.get('status','')} "
                    f"{value.get('objective','')} [{value.get('progress_current',0)}/{value.get('progress_target',0)}] "
                    f"reward={value.get('reward','')}"
                )
            elif kind == _FORESHADOW_KIND:
                lines.append(
                    f"- FORESHADOW {value.get('display_name') or key}: {value.get('status','')} "
                    f"seed={value.get('seed','')} payoff={value.get('payoff','')}"
                )
        for check in reversed(list_checks(db, chat_id, session_id, through_rowid=through_rowid, limit=3)):
            lines.append(
                f"- CHECK {check['domain']}: {check['actor']} attempts {check['action']} | "
                f"DC {check['dc']} roll {check['roll']} mod {check['modifier']} => {check['outcome']}"
            )
        result = "\n".join(lines)
        return result[:_MAX_CONTEXT]
