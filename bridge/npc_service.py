"""Application service for durable, reversible NPC state."""

from __future__ import annotations

import json
import re
import time
from dataclasses import replace
from typing import Any

import bridge.limits as _limits
from bridge.npc_repository import (
    delete_npc_entity,
    delete_npc_field,
    delete_npc_history_from_row,
    find_npc_by_name_or_alias,
    get_npc_extraction_coverage,
    insert_npc_entity,
    insert_npc_field_change,
    list_npc_entities,
    list_npc_field_history,
    load_npc_fields,
    load_npc_fields_as_of,
    purge_npc_session_rows,
    set_npc_entity_last_seen,
    set_npc_extraction_coverage,
    update_npc_entity_seen,
    upsert_npc_field,
)
from bridge.npc_types import NpcApplyResult, NpcExtractionGroup, NpcFieldState, NpcOperation
from bridge.sqlite_store import write_transaction

_FIXED_FIELDS = frozenset({"appearance", "voice", "background", "canon"})
_MUTABLE_FIELDS = frozenset({"role", "location", "agenda", "relationship", "mood", "secrets", "status"})
_LIST_FIELDS = frozenset({"secrets", "status"})
_ALLOWED_OPERATIONS = frozenset({"set", "append", "remove"})


def normalize_npc_name(value: str) -> str:
    return " ".join(str(value or "").split()).strip().casefold()


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalize_known_by(values: Any) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    result = []
    seen = set()
    for value in values:
        text = _clean_text(value)
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return tuple(result)


def _expected_mode(field_key: str) -> str | None:
    if field_key in _FIXED_FIELDS:
        return "fixed"
    if field_key in _MUTABLE_FIELDS:
        return "mutable"
    return None


def _normalize_list_value(value: Any) -> list[str]:
    source = value if isinstance(value, list) else [value]
    result = []
    seen = set()
    for item in source:
        text = _clean_text(item)
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def validate_npc_operation(operation: NpcOperation) -> NpcOperation | None:
    field_key = str(operation.field_key or "").strip().casefold()
    expected_mode = _expected_mode(field_key)
    op = str(operation.operation or "").strip().casefold()
    mode = str(operation.field_mode or "").strip().casefold()
    visibility = str(operation.visibility or "").strip().casefold()
    known_by = _normalize_known_by(operation.known_by)

    if expected_mode is None or op not in _ALLOWED_OPERATIONS or mode != expected_mode:
        return None
    if visibility not in {"shared", "restricted"}:
        return None
    if visibility == "restricted" and not known_by:
        raise ValueError("restricted NPC field requires known_by")
    if field_key == "secrets" and visibility != "restricted":
        return None
    if op in {"append", "remove"} and field_key not in _LIST_FIELDS:
        return None

    value = (
        _normalize_list_value(operation.value)
        if op == "set" and field_key in _LIST_FIELDS
        else _clean_text(operation.value)
    )
    if value in ("", []):
        return None
    return NpcOperation(field_key, op, value, mode, visibility, known_by)


def _all_entity_names(entity: Any) -> set[str]:
    return {normalize_npc_name(entity.canonical_name), *(normalize_npc_name(alias) for alias in entity.aliases)}


_FIELD_ORDER = (
    "role",
    "appearance",
    "voice",
    "background",
    "canon",
    "location",
    "agenda",
    "relationship",
    "mood",
    "status",
    "secrets",
)


def _mentions_name(text: str, name: str) -> bool:
    haystack = " ".join(str(text or "").split()).casefold()
    needle = normalize_npc_name(name)
    if not haystack or not needle:
        return False
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack, flags=re.UNICODE) is not None


def _scene_participant_names(db: Any, chat_id: str, session_id: str, *, through_rowid: int | None) -> set[str]:
    row = db.execute(
        "SELECT state_json,updated_through_rowid FROM scene_states WHERE chat_id=? AND session_id=?",
        (chat_id, session_id),
    ).fetchone()
    if row is None:
        return set()
    if through_rowid is not None and int(row[1]) > int(through_rowid):
        return set()
    try:
        state = json.loads(str(row[0] or "{}"))
    except (TypeError, json.JSONDecodeError):
        return set()
    participants = state.get("participants") if isinstance(state, dict) else None
    names: list[str] = []
    if isinstance(participants, dict):
        names.extend(str(key) for key in participants)
        for value in participants.values():
            if isinstance(value, dict) and value.get("name"):
                names.append(str(value["name"]))
    elif isinstance(participants, list):
        for value in participants:
            if isinstance(value, str):
                names.append(value)
            elif isinstance(value, dict) and value.get("name"):
                names.append(str(value["name"]))
    return {normalize_npc_name(name) for name in names if normalize_npc_name(name)}


def _render_npc_field_value(value: Any) -> str:
    if isinstance(value, list):
        return "; ".join(_clean_text(item) for item in value if _clean_text(item))
    return _clean_text(value)


class NpcService:
    def apply_group(
        self,
        db: Any,
        chat_id: str,
        session_id: str,
        group: NpcExtractionGroup,
        *,
        source_rowid: int,
        primary_name: str,
        user_name: str,
    ) -> NpcApplyResult:
        canonical = normalize_npc_name(group.name)
        blocked_names = {normalize_npc_name(primary_name), normalize_npc_name(user_name)}
        if not canonical or canonical in blocked_names:
            return NpcApplyResult(0, max(1, len(group.operations)))

        validated: list[NpcOperation] = []
        rejected = 0
        for operation in group.operations:
            item = validate_npc_operation(operation)
            if item is None:
                rejected += 1
            else:
                validated.append(item)
        if not validated:
            return NpcApplyResult(0, rejected)

        entities = list_npc_entities(db, chat_id, session_id)
        entity = find_npc_by_name_or_alias(db, chat_id, session_id, canonical)
        if entity is None:
            candidate_names = {canonical, *(normalize_npc_name(alias) for alias in group.aliases if _clean_text(alias))}
            for existing in entities:
                if candidate_names & _all_entity_names(existing):
                    return NpcApplyResult(0, rejected + len(validated))
            aliases = tuple(
                _clean_text(alias)
                for alias in group.aliases
                if _clean_text(alias)
                and normalize_npc_name(alias) != canonical
                and normalize_npc_name(alias) not in blocked_names
            )
        else:
            aliases = ()

        now = time.time()
        applied = 0
        with write_transaction(db):
            if entity is None:
                npc_id = insert_npc_entity(
                    db,
                    chat_id,
                    session_id,
                    canonical,
                    _clean_text(group.name),
                    aliases,
                    int(source_rowid),
                    now,
                )
            else:
                npc_id = entity.npc_id

            fields = load_npc_fields(db, npc_id)
            for operation in validated:
                before = fields.get(operation.field_key)
                if before is not None and before.field_mode == "fixed":
                    wanted = operation.value
                    if before.value != wanted:
                        rejected += 1
                    continue

                after_value = self._next_value(before, operation)
                if after_value is _NO_CHANGE:
                    continue
                if after_value is _REMOVE_FIELD:
                    after = None
                else:
                    after = NpcFieldState(
                        npc_id=npc_id,
                        field_key=operation.field_key,
                        value=after_value,
                        field_mode=operation.field_mode,
                        visibility=operation.visibility,
                        known_by=operation.known_by,
                        updated_rowid=int(source_rowid),
                        updated_at=now,
                    )

                insert_npc_field_change(
                    db,
                    npc_id,
                    operation.field_key,
                    operation.operation,
                    before,
                    after,
                    int(source_rowid),
                    now,
                )
                if after is None:
                    delete_npc_field(db, npc_id, operation.field_key)
                    fields.pop(operation.field_key, None)
                else:
                    upsert_npc_field(db, after)
                    fields[operation.field_key] = after
                applied += 1

            if applied:
                update_npc_entity_seen(db, npc_id, int(source_rowid), now)

        return NpcApplyResult(applied, rejected, npc_id if applied or entity is not None else 0)

    @staticmethod
    def _next_value(before: NpcFieldState | None, operation: NpcOperation) -> Any:
        if operation.operation == "set":
            return operation.value if before is None or before.value != operation.value else _NO_CHANGE

        current = list(before.value) if before is not None and isinstance(before.value, list) else []
        item = _clean_text(operation.value)
        key = item.casefold()
        matches = [index for index, value in enumerate(current) if _clean_text(value).casefold() == key]

        if operation.operation == "append":
            if matches:
                return _NO_CHANGE
            return [*current, item]

        if not matches:
            return _NO_CHANGE
        current.pop(matches[0])
        return current if current else _REMOVE_FIELD

    def context_for_prompt(
        self,
        db: Any,
        chat_id: str,
        session: dict[str, str],
        fields: dict[str, str],
        query: str,
        history_rows: list[tuple[str, str]],
        *,
        through_rowid: int | None = None,
    ) -> str:
        session_id = str(session["session_id"])
        entities = [
            entity
            for entity in list_npc_entities(db, chat_id, session_id)
            if through_rowid is None or entity.first_seen_rowid <= int(through_rowid)
        ]
        if not entities:
            return ""

        owners: dict[str, set[int]] = {}
        for entity in entities:
            for name in _all_entity_names(entity):
                if name:
                    owners.setdefault(name, set()).add(entity.npc_id)

        unique_query: set[int] = set()
        ambiguous_query: set[int] = set()
        for name, npc_ids in owners.items():
            if not _mentions_name(query, name):
                continue
            if len(npc_ids) == 1:
                unique_query.update(npc_ids)
            else:
                ambiguous_query.update(npc_ids)

        history_text = "\n".join(str(content) for _role, content in history_rows[-12:])
        unique_history: set[int] = set()
        for name, npc_ids in owners.items():
            if len(npc_ids) == 1 and _mentions_name(history_text, name):
                unique_history.update(npc_ids)

        scene_names = _scene_participant_names(
            db,
            chat_id,
            session_id,
            through_rowid=through_rowid,
        )
        scene_ids: set[int] = set()
        for name in scene_names:
            npc_ids = owners.get(name, set())
            if len(npc_ids) == 1:
                scene_ids.update(npc_ids)

        explicitly_resolved = unique_query | unique_history | scene_ids
        active_character = normalize_npc_name(fields.get("name") or "")
        ranked = []
        for entity in entities:
            if entity.npc_id in ambiguous_query and entity.npc_id not in explicitly_resolved:
                continue
            current_fields = (
                load_npc_fields(db, entity.npc_id)
                if through_rowid is None
                else load_npc_fields_as_of(db, entity.npc_id, int(through_rowid))
            )
            visible = {}
            for key, state in current_fields.items():
                if state.visibility == "restricted":
                    allowed = {normalize_npc_name(name) for name in state.known_by}
                    if not active_character or active_character not in allowed:
                        continue
                rendered = _render_npc_field_value(state.value)
                if rendered:
                    visible[key] = rendered
            if not visible:
                continue
            last_seen = entity.last_seen_rowid
            if through_rowid is not None:
                last_seen = min(last_seen, int(through_rowid))
            ranked.append(
                (
                    entity.npc_id in scene_ids,
                    entity.npc_id in unique_query,
                    entity.npc_id in unique_history,
                    last_seen,
                    entity.npc_id,
                    entity,
                    visible,
                )
            )

        ranked.sort(key=lambda item: item[:5], reverse=True)
        blocks: list[str] = []
        used = 0
        for *_score, entity, visible in ranked[: _limits.NPC_CONTEXT_MAX_NPCS]:
            lines = [f"NPC: {entity.display_name}"]
            for key in _FIELD_ORDER:
                value = visible.get(key)
                if value:
                    lines.append(f"{key.replace('_', ' ').title()}: {value}")
            block = "\n".join(lines)
            separator = 2 if blocks else 0
            remaining = _limits.NPC_CONTEXT_MAX_CHARS - used - separator
            if remaining <= 0:
                break
            if len(block) > remaining:
                block = block[:remaining].rstrip()
            if not block:
                break
            blocks.append(block)
            used += separator + len(block)
            if used >= _limits.NPC_CONTEXT_MAX_CHARS:
                break
        return "\n\n".join(blocks)

    def rollback_from_row(self, db: Any, chat_id: str, session_id: str, rowid: int) -> int:
        cutoff = int(rowid)
        now = time.time()
        removed = 0
        with write_transaction(db):
            for entity in list_npc_entities(db, chat_id, session_id):
                history = list_npc_field_history(db, entity.npc_id)
                doomed = [change for change in history if change.source_rowid >= cutoff]
                if not doomed:
                    continue
                affected = {change.field_key for change in doomed}
                for field_key in affected:
                    prior = [
                        change for change in history if change.field_key == field_key and change.source_rowid < cutoff
                    ]
                    prior_state = prior[-1].after if prior else None
                    if prior_state is None:
                        delete_npc_field(db, entity.npc_id, field_key)
                    else:
                        upsert_npc_field(
                            db,
                            replace(
                                prior_state,
                                updated_rowid=prior[-1].source_rowid,
                                updated_at=prior[-1].created_at,
                            ),
                        )
                removed += delete_npc_history_from_row(db, entity.npc_id, cutoff)

                remaining_history = list_npc_field_history(db, entity.npc_id)
                remaining_fields = load_npc_fields(db, entity.npc_id)
                if entity.first_seen_rowid >= cutoff and not remaining_history and not remaining_fields:
                    delete_npc_entity(db, entity.npc_id)
                    continue
                latest = max((change.source_rowid for change in remaining_history), default=entity.first_seen_rowid)
                set_npc_entity_last_seen(db, entity.npc_id, latest, now)

            coverage = get_npc_extraction_coverage(db, chat_id, session_id)
            if coverage >= cutoff:
                set_npc_extraction_coverage(db, chat_id, session_id, max(0, cutoff - 1), now)
        return removed

    def purge_session(self, db: Any, chat_id: str, session_id: str) -> int:
        with write_transaction(db):
            return purge_npc_session_rows(db, chat_id, session_id)


_NO_CHANGE = object()
_REMOVE_FIELD = object()
