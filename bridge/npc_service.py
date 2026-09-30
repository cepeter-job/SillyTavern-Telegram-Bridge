"""Application service for durable, reversible NPC state."""

from __future__ import annotations

import time
from dataclasses import replace

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


def _clean_text(value) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalize_known_by(values) -> tuple[str, ...]:
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


def _normalize_list_value(value) -> list[str]:
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


def _validated_operation(operation: NpcOperation) -> NpcOperation | None:
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


def _all_entity_names(entity) -> set[str]:
    return {normalize_npc_name(entity.canonical_name), *(normalize_npc_name(alias) for alias in entity.aliases)}


class NpcService:
    def apply_group(
        self,
        db,
        chat_id: str,
        session_id: str,
        group: NpcExtractionGroup,
        *,
        source_rowid: int,
        primary_name: str,
        user_name: str,
    ) -> NpcApplyResult:
        canonical = normalize_npc_name(group.name)
        if not canonical or canonical in {normalize_npc_name(primary_name), normalize_npc_name(user_name)}:
            return NpcApplyResult(0, max(1, len(group.operations)))

        validated: list[NpcOperation] = []
        rejected = 0
        for operation in group.operations:
            item = _validated_operation(operation)
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
                if _clean_text(alias) and normalize_npc_name(alias) != canonical
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
    def _next_value(before: NpcFieldState | None, operation: NpcOperation):
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

    def rollback_from_row(self, db, chat_id: str, session_id: str, rowid: int) -> int:
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

    def purge_session(self, db, chat_id: str, session_id: str) -> int:
        with write_transaction(db):
            return purge_npc_session_rows(db, chat_id, session_id)


_NO_CHANGE = object()
_REMOVE_FIELD = object()
