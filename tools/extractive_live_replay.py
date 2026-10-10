"""Read-only local-core continuation replay; never claim a full live wire capture."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from bridge.card_content import card_fields, read_png_chara, safe_character_path
from bridge.context_hybrid_types import MAX_HISTORY_ROWS
from bridge.generation import build_chat_messages
from bridge.memory_artifact_store import read_scene_block
from bridge.memory_contracts import MemoryPromptContext, expand_memory_query
from bridge.memory_scope_store import read_episodic_block, resolve_memory_scope, validate_memory_blocks
from bridge.narrative_context import narrative_context_for_session
from bridge.session_repository import load_session_row
from bridge.summary_archive_store import read_summary_block
from tools.extractive_context import evaluate_extractive_context

INSTRUCTION = (
    "Continue the previous assistant response from its exact ending. "
    "Do not repeat any existing text. Output only the continuation."
)
MISSING_COMPONENTS = (
    "external_recall",
    "npc_context",
    "simulation_context",
    "rag_context",
    "group_context",
    "locked_action_and_novel_contracts",
)


class LocalPersona:
    """Read local persona metadata without opening the native HTTP API."""

    def __init__(self, settings, persona_id: str):
        self.personas: dict = {}
        self.descriptions: dict = {}
        if persona_id:
            path = settings.native_persona_settings_file
            if path.stat().st_size > 4_000_000:
                raise ValueError("persona_resource_bound")
            power = json.loads(path.read_text(encoding="utf-8"))["power_user"]
            self.personas = power["personas"]
            self.descriptions = power["persona_descriptions"]
            if persona_id not in self.personas:
                raise ValueError("persona_unavailable")

    def name(self, persona_id: str) -> str:
        return str(self.personas.get(persona_id, ""))

    def get(self, persona_id: str) -> dict | None:
        if not persona_id:
            return None
        return {
            "name": self.name(persona_id),
            "description": str(self.descriptions.get(persona_id, {}).get("description", ""))[:4000],
        }


def _sample(db, ordinal: int, chat: str, sid: str, character: str, settings) -> dict:
    card = safe_character_path(character, app_settings=settings)
    if card is None or card.stat().st_size > 16_000_000:
        raise ValueError("card_unavailable")
    fields = card_fields(read_png_chara(card), app_settings=settings)
    session = load_session_row(db, chat, sid)
    scope = resolve_memory_scope(db, chat, session, fields)
    rows = db.execute(
        "SELECT role,content FROM messages WHERE chat_id=? AND session_id=? ORDER BY created_at,id LIMIT ?",
        (chat, sid, MAX_HISTORY_ROWS + 1),
    ).fetchall()
    if scope is None or not rows or len(rows) > MAX_HISTORY_ROWS:
        raise ValueError("scope_or_history_bound")
    summary = read_summary_block(db, scope, INSTRUCTION)
    scene = read_scene_block(db, scope)
    query = expand_memory_query(INSTRUCTION, scope.principals, scene.text)
    episodes = read_episodic_block(db, scope, query)
    summary, scene, episodes = validate_memory_blocks(db, scope, (summary, scene, episodes))
    messages = build_chat_messages(
        session,
        fields,
        INSTRUCTION,
        rows,
        persona_service=LocalPersona(settings, session["persona_id"]),
        session_summary=summary.text,
        scene_context=scene.text,
        episodic_context=episodes.text,
        narrative_context=narrative_context_for_session(db, chat, sid, "story"),
        memory_prompt=MemoryPromptContext(scope=scope, selection_mode="shadow"),
        app_settings=settings,
        defer_compaction=True,
    )
    result = evaluate_extractive_context(db, scope, messages, query=INSTRUCTION)
    return {"sample": ordinal, "source_rows": len(rows), **result.metrics}


def build_live_report(database: Path, settings) -> dict:
    samples = []
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=9)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        scopes = db.execute(
            "SELECT l.chat_id,l.session_id,s.character_file FROM memory_layer_state l JOIN sessions s "
            "ON s.chat_id=l.chat_id AND s.session_id=l.session_id AND s.created_at=l.session_created_at "
            "WHERE l.layer='summary' ORDER BY s.created_at LIMIT 65"
        ).fetchall()
        if len(scopes) > 64:
            raise ValueError("live_scope_resource_bound")
        for ordinal, (chat, sid, card) in enumerate(scopes, 1):
            try:
                sample = _sample(db, ordinal, chat, sid, card, settings)
            except (ValueError, TypeError, KeyError, OSError, sqlite3.Error):
                sample = {
                    "sample": ordinal,
                    "candidate_status": "fallback",
                    "reason": "local_core_reconstruction_unavailable",
                    "baseline_tokens": None,
                    "candidate_tokens": None,
                }
            samples.append(sample)
        if db.total_changes:
            raise ValueError("read_only_replay_modified_database")
    complete_core = bool(samples) and all(type(s["baseline_tokens"]) is int for s in samples)
    baseline = sum(s["baseline_tokens"] for s in samples) if complete_core else None
    candidate = sum(s["candidate_tokens"] for s in samples) if complete_core else None
    return {
        "schema_version": 1,
        "type": "read_only_local_core_continuation_replay",
        "complete_live_prompt_captured": False,
        "provider_measured_reduction": None,
        "missing_prompt_components": list(MISSING_COMPONENTS),
        "included_prompt_components": [
            "card",
            "system",
            "world",
            "persona",
            "author_note",
            "history",
            "scoped_summary",
            "scoped_scene",
            "scoped_episodes",
            "narrative",
            "current_input",
        ],
        "production_database_writes": 0,
        "production_transcript_exported": False,
        "provider_requests": 0,
        "production_activation_allowed": False,
        "semantic_continuity_proven": False,
        "independent_human_review_complete": False,
        "estimated": {
            "all_local_core_denominators_available": complete_core,
            "local_core_baseline_tokens": baseline,
            "local_core_candidate_tokens": candidate,
            "local_core_aggregate_reduction_fraction": 1 - candidate / baseline if baseline else None,
        },
        "cases": samples,
        "limitations": [
            "Counterfactual continuation on a read snapshot; not actual dispatched provider requests.",
            "Missing fixed payloads must not be treated as empty in a full-live efficiency claim.",
            "Adding omitted fixed payloads unchanged can only lower the estimated savings fraction.",
            "No private transcript, source identity, reader identity or account credential is exported.",
            "A read snapshot is not authority to dispatch after the live source changes.",
        ],
    }
