"""Restore the NPC source prefix inside an existing database transaction."""

from __future__ import annotations

import sqlite3
from dataclasses import replace

from bridge.npc_repository import (
    delete_npc_entity,
    delete_npc_field,
    delete_npc_history_from_row,
    get_npc_extraction_coverage,
    list_npc_entities,
    list_npc_field_history,
    load_npc_fields,
    set_npc_entity_last_seen,
    set_npc_extraction_coverage,
    upsert_npc_field,
)
from bridge.repository_contracts import require_active_transaction


def rollback_from_row(db: sqlite3.Connection, chat_id: str, session_id: str, rowid: int, *, now: float) -> int:
    require_active_transaction(db)
    cutoff = int(rowid)
    removed = 0
    for entity in list_npc_entities(db, chat_id, session_id):
        history = list_npc_field_history(db, entity.npc_id)
        doomed = [change for change in history if change.source_rowid >= cutoff]
        if not doomed:
            continue
        affected = {change.field_key for change in doomed}
        for field_key in affected:
            prior = [change for change in history if change.field_key == field_key and change.source_rowid < cutoff]
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
