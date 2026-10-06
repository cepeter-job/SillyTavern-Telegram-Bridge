"""Restore checkpoint-owned state through the existing domain repositories."""

from __future__ import annotations

import json
import sqlite3
import time

from bridge.checkpoint_remap import remap_checkpoint
from bridge.conversation_lifecycle import (
    configure_conversation,
    conversation_state,
    initialize_conversation,
    mark_started,
)
from bridge.director_repository import restore_director_snapshot
from bridge.ending_repository import restore_ending_goal_snapshot
from bridge.generation_settings_repository import ensure_settings_row
from bridge.group_repository import store_group_state_row
from bridge.memory_snapshot_store import restore_local_memory_snapshot
from bridge.meta_repository import store_meta_value
from bridge.model_selection import set_director_reasoning, set_task_model, set_utility_reasoning
from bridge.narrative_arc_repository import store_arc_row
from bridge.narrative_checkpoints import NarrativeCheckpoint
from bridge.narrative_repository import load_narrative_clock, restore_narrative_snapshot
from bridge.narrative_settings import normalize_narrative_settings, save_session_narrative_settings
from bridge.npc_repository import restore_npc_snapshot
from bridge.scene_repository import upsert_scene_state_if_fresh
from bridge.simulation_snapshot import restore_simulation_snapshot


def restore_checkpoint_state(
    db: sqlite3.Connection,
    chat_id: str,
    target: str,
    checkpoint: NarrativeCheckpoint,
    rowid_map: dict[int, int],
) -> None:
    if not db.in_transaction:
        raise RuntimeError("Checkpoint restoration requires its caller-owned transaction")
    data = remap_checkpoint(checkpoint.payload, rowid_map)
    now = time.time()
    config = data["config"]
    save_session_narrative_settings(db, chat_id, target, normalize_narrative_settings(data["settings"]))
    ensure_settings_row(db, chat_id, target, config["generation"])
    for task, model in config["task_models"].items():
        set_task_model(db, chat_id, target, model, task)
    set_utility_reasoning(db, chat_id, target, config["utility_reasoning"])
    set_director_reasoning(db, chat_id, target, config["director_reasoning"])
    for name, value in config["preferences"].items():
        if name not in {"humanizer", "grounded_user", "expression_mode", "image_model", "image_size", "image_style"}:
            raise ValueError("Unexpected checkpoint preference")
        if value:
            store_meta_value(db, f"{name}:{chat_id}:{target}", str(value))
    initialize_conversation(db, chat_id, target)
    conversation = config["conversation"]
    configure_conversation(db, chat_id, target, conversation["mode"], conversation["strategy"])
    mark_started(db, chat_id, target, conversation_state(db, chat_id, target).epoch)
    if config["group"] is not None:
        store_group_state_row(db, chat_id, target, **config["group"], updated_at=now)
    restore_npc_snapshot(db, chat_id, target, data["memory"].get("npcs", []))
    restore_simulation_snapshot(db, chat_id, target, data["memory"].get("simulation", {}))
    physical = data.get("physical_scene")
    if physical:
        upsert_scene_state_if_fresh(db, chat_id, target, physical["state_json"], physical["updated_through_rowid"], now)
    restore_local_memory_snapshot(db, chat_id, target, data["memory"])
    for item in data["arcs"]:
        arc = item | {
            "open_questions": json.loads(item["open_questions_json"]),
            "related_threads": json.loads(item["related_threads_json"]),
            "evidence": json.loads(item["evidence_json"]),
        }
        store_arc_row(db, chat_id, target, arc)
    restore_narrative_snapshot(db, chat_id, target, data["state"], data["scenes"], data["threads"], now=now)
    clock = load_narrative_clock(db, chat_id, target)
    if clock is None:
        raise ValueError("The restored narrative clock is missing")
    restore_director_snapshot(
        db,
        chat_id,
        target,
        data["director"],
        data["director_history"],
        settings_revision=clock["settings_revision"],
        rewrite_revision=clock["rewrite_revision"],
        now=now,
    )
    restore_ending_goal_snapshot(db, chat_id, target, data["ending"], data["ending_goal_history"], now)
