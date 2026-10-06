"""Compose a coherent restoration snapshot through the existing domain owners."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from typing import Any

from bridge.conversation_lifecycle import conversation_state
from bridge.director_repository import director_decision_history, load_director_state
from bridge.ending_service import ending_goal_history, load_ending_state
from bridge.generation_settings import get_generation_settings
from bridge.group_repository import load_group_state_row
from bridge.memory_snapshot_store import snapshot_local_memory
from bridge.meta_repository import load_meta_value, session_task_models
from bridge.model_selection import director_reasoning_for_session, utility_reasoning_for_session
from bridge.narrative_context import load_narrative_state
from bridge.narrative_repository import narrative_snapshot_rows
from bridge.narrative_settings import load_session_narrative_settings
from bridge.npc_repository import snapshot_npc_state
from bridge.scene_repository import load_scene_state_row
from bridge.session_repository import load_session_row
from bridge.simulation_snapshot import snapshot_simulation_state
from bridge.transcript_repository import transcript_prefix_fingerprint

_CONFIG_PREFIXES = ("humanizer", "grounded_user", "expression_mode", "image_model", "image_size", "image_style")


def capture_pre_finale_state(
    db: sqlite3.Connection, chat_id: str, session_id: str, through_rowid: int
) -> dict[str, Any]:
    session = load_session_row(db, chat_id, session_id)
    if session is None:
        raise ValueError("This story no longer exists")
    entities = narrative_snapshot_rows(db, chat_id, session_id, through_rowid)
    director = load_director_state(db, chat_id, session_id)
    physical = load_scene_state_row(db, chat_id, session_id)
    if physical is not None and physical[1] > through_rowid:
        physical = None
    memory = snapshot_local_memory(db, chat_id, session_id, through_rowid)
    memory["npcs"] = snapshot_npc_state(db, chat_id, session_id, through_rowid)
    memory["simulation"] = snapshot_simulation_state(db, chat_id, session_id, through_rowid)
    group = load_group_state_row(db, chat_id, session_id)
    captured = {
        "format_version": 1,
        "origin": {"chat_id": chat_id, "session_id": session_id},
        "session": session,
        "settings": load_session_narrative_settings(db, chat_id, session_id).to_dict(),
        "state": asdict(load_narrative_state(db, chat_id, session_id)),
        "ending": asdict(load_ending_state(db, chat_id, session_id)),
        "ending_goal_history": [asdict(row) for row in ending_goal_history(db, chat_id, session_id)],
        "director": {
            key: director[key]
            for key in (
                "goal",
                "active_direction",
                "active_proposal_json",
                "direction_scope",
                "direction_source",
                "accepted_scene_id",
                "accepted_through_rowid",
                "accepted_rewrite_revision",
                "accepted_settings_revision",
                "state_revision",
                "direction_until_turn",
                "last_director_turn",
            )
        }
        | {"arc_guidance_json": director.get("arc_guidance_json", "{}")},
        "director_history": director_decision_history(db, chat_id, session_id, limit=200),
        "transcript": transcript_prefix_fingerprint(db, chat_id, session_id, through_rowid),
        "physical_scene": {"state_json": physical[0], "updated_through_rowid": physical[1]} if physical else None,
        "memory": memory,
        "config": {
            "task_models": session_task_models(db, chat_id, session_id),
            "utility_reasoning": utility_reasoning_for_session(db, chat_id, session_id),
            "director_reasoning": director_reasoning_for_session(db, chat_id, session_id),
            "generation": get_generation_settings(db, chat_id, session_id),
            "conversation": asdict(conversation_state(db, chat_id, session_id)),
            "preferences": {key: load_meta_value(db, f"{key}:{chat_id}:{session_id}", "") for key in _CONFIG_PREFIXES},
            "group": dict(
                zip(
                    (
                        "title",
                        "enabled",
                        "turn_index",
                        "mode",
                        "forced_speaker",
                        "members_json",
                        "turn_user_id",
                        "turn_users_json",
                    ),
                    group,
                    strict=True,
                )
            )
            if group
            else None,
        },
        **entities,
    }
    encoded = json.dumps(captured, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode()) > 1048576:
        raise ValueError("The pre-finale restoration snapshot is too large; the finale was not started")
    return captured
