"""Canonical tracker publication and lifecycle application service."""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from bridge.simulation_checks import actor_modifier, classify_check, perform_check
from bridge.simulation_context import simulation_context_for_prompt
from bridge.simulation_extraction import normalize_simulation_payload
from bridge.simulation_mechanics import SimulationMechanics
from bridge.simulation_repository import (
    bump_revision,
    list_states,
    load_state,
    load_states_as_of,
    validate_publication,
)
from bridge.simulation_repository import (
    purge_session as purge_repository_session,
)
from bridge.simulation_repository import (
    rollback_from_row as rollback_repository_from_row,
)
from bridge.simulation_values import KINDS as _ALLOWED_KINDS
from bridge.simulation_values import key as _key
from bridge.sqlite_store import write_transaction

_FACTION_KIND = "faction"
_QUEST_KIND = "quest"
_FORESHADOW_KIND = "foreshadowing"


class SimulationService(SimulationMechanics):
    classify_check = staticmethod(classify_check)
    perform_check = staticmethod(perform_check)
    actor_modifier = staticmethod(actor_modifier)
    context_for_prompt = staticmethod(simulation_context_for_prompt)

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
        if kind == "actor" and entity_key == "user":
            records = (
                [
                    (k, key, value, source)
                    for k, key, value, source, _ in list_states(db, chat_id, session_id, kind="actor")
                ]
                if through_rowid is None
                else load_states_as_of(db, chat_id, session_id, through_rowid=through_rowid)
            )
            collections = {
                value["collection"]: value["entries"]
                for k, key, value, _ in records
                if k == "actor" and key.startswith("user:")
            }
            return (
                {name: collections.get(name, []) for name in ("inventory", "skills", "conditions")}
                if collections
                else None
            )
        if through_rowid is None:
            row = load_state(db, chat_id, session_id, kind, entity_key)
            return dict(row[0]) if row else None
        for item_kind, item_key, value, _rowid in load_states_as_of(
            db, chat_id, session_id, through_rowid=int(through_rowid)
        ):
            if item_kind == kind and item_key == entity_key:
                return dict(value)
        return None

    def apply_payload(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session_id: str,
        payload: dict[str, Any],
        *,
        source_rowid: int,
    ) -> None:
        payload = normalize_simulation_payload(payload)
        now = time.time()
        with write_transaction(db):
            accepted, role = validate_publication(db, chat_id, session_id, int(source_rowid))
            if not accepted:
                return
            self._relationship_updates(db, chat_id, session_id, payload, source_rowid, now, tick=role == "assistant")
            self._agenda_updates(db, chat_id, session_id, payload, source_rowid, now, tick=role == "assistant")
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
            bump_revision(db, chat_id, session_id)

    def rollback_from_row(self, db: sqlite3.Connection, chat_id: str, session_id: str, rowid: int) -> int:
        with write_transaction(db):
            return rollback_repository_from_row(db, chat_id, session_id, int(rowid), now=time.time())

    def purge_session(self, db: sqlite3.Connection, chat_id: str, session_id: str) -> None:
        with write_transaction(db):
            purge_repository_session(db, chat_id, session_id)
