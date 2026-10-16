"""Durable episodic-memory extraction from stable conversation segments."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from bridge.episodic_memory import store_episodic_memory
from bridge.generation_settings import get_generation_settings
from bridge.json_fences import unfence_json
from bridge.memory_artifact_store import CLASSIFIED_AUDIENCE_PROMPT
from bridge.memory_contracts import MemoryFact
from bridge.memory_fact_store import accept_source_facts, classified_audience
from bridge.memory_response import generate_memory_response
from bridge.memory_store import MemorySource
from bridge.model_selection import task_model_for_session, utility_reasoning_for_session
from bridge.narrative_repository import load_narrative_clock
from bridge.provider_port import ProviderPort
from bridge.settings import AppSettings
from bridge.sqlite_store import write_transaction

EPISODIC_KINDS = frozenset({"scene_event", "relationship_change", "fact", "goal", "world_change", "secret"})
EPISODIC_MIN_IMPORTANCE = 0.65
EPISODIC_MAX_EVENTS = 6
EPISODIC_MAX_SUMMARY_CHARS = 800
EPISODIC_MAX_OUTPUT_TOKENS = 900


@dataclass(frozen=True)
class EpisodicCandidate:
    kind: str
    importance: float
    summary: str
    visibility: str = "shared"
    known_by: tuple[str, ...] = ()


def parse_episodic_candidates(source: str) -> list[EpisodicCandidate]:
    try:
        payload = json.loads(unfence_json(source))
    except (TypeError, ValueError) as exc:
        raise ValueError("episodic memory response is not valid JSON") from exc
    no_memory_reason = ""
    if isinstance(payload, dict):
        reason = payload.get("no_memory_reason")
        no_memory_reason = reason.strip() if isinstance(reason, str) else ""
        payload = payload.get("memories")
    if not isinstance(payload, list):
        raise ValueError("episodic memory response must be a JSON array")
    if not payload and len(no_memory_reason) < 8:
        raise ValueError("episodic memory empty result requires a reason")
    result: list[EpisodicCandidate] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip().casefold()
        summary = str(item.get("summary") or "").strip()
        visibility, known_by = classified_audience(item.get("visibility"), item.get("known_by"))
        try:
            importance = float(item.get("importance"))
        except (TypeError, ValueError):
            continue
        if (
            kind not in EPISODIC_KINDS
            or visibility not in {"shared", "restricted"}
            or (visibility == "restricted" and not known_by)
            or not summary
            or not 0 <= importance <= 1
            or importance < EPISODIC_MIN_IMPORTANCE
        ):
            continue
        result.append(
            EpisodicCandidate(
                kind=kind,
                importance=importance,
                summary=summary[:EPISODIC_MAX_SUMMARY_CHARS],
                visibility=visibility,
                known_by=known_by if visibility == "restricted" else (),
            )
        )
        if len(result) >= EPISODIC_MAX_EVENTS:
            break
    if payload and not result:
        raise ValueError("episodic memory populated result has no accepted candidates")
    return result


@dataclass(frozen=True)
class EpisodicExtractionResult:
    status: str
    inserted: int = 0
    memory_ids: tuple[int, ...] = ()


def extract_episodic_memories_result(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict[str, str],
    *,
    source_text: str,
    source_start_rowid: int,
    source_end_rowid: int,
    provider_port: ProviderPort,
    app_settings: AppSettings,
    expected_source: tuple[float, int, int] | None = None,
    source_valid: Callable[[], bool] | None = None,
    source_ref: MemorySource | None = None,
) -> EpisodicExtractionResult:
    if len(source_text) > 50000:
        raise ValueError("Episodic input is too large; supply bounded canonical source parts")
    if source_ref is not None and (
        source_text != source_ref.content
        or source_start_rowid != source_ref.start_id
        or source_end_rowid != source_ref.end_id
    ):
        raise ValueError("Extraction text must be the exact canonical source part")
    if not str(source_text or "").strip():
        if source_ref is not None and accept_source_facts(db, source_ref, [], source_valid=source_valid) is None:
            return EpisodicExtractionResult("stale")
        return EpisodicExtractionResult("complete")
    if db.in_transaction:
        raise RuntimeError("Episodic extraction cannot call a provider inside a transaction")
    session_id = str(session["session_id"])
    source_clock = load_narrative_clock(db, chat_id, session_id)
    if source_clock is None or (source_valid is not None and not source_valid()):
        return EpisodicExtractionResult("stale")
    identity_fields = ("session_created_at", "history_revision", "latest_rowid")
    if expected_source is not None and tuple(source_clock[key] for key in identity_fields) != expected_source:
        return EpisodicExtractionResult("stale")
    messages = [
        {
            "role": "system",
            "content": (
                "Extract only durable roleplay memories from the transcript segment. "
                "Keep irreversible events, established facts, goals, relationship changes, "
                "world changes, and secrets. Exclude transient scene posture, ordinary dialogue, "
                "style instructions, and speculation stated as fact. Preserve uncertainty. "
                'Return only one JSON object with a "memories" array. When no durable event qualifies, '
                'return {"memories":[],"no_memory_reason":"concise reason"}; never return an unexplained '
                "empty array. Each memory has kind, importance (0 to 1), "
                "summary, visibility, and known_by. Include at most six qualifying memories, "
                "with importance at least 0.65 and summaries at most 800 characters each. "
                "visibility must be shared or restricted. "
                + CLASSIFIED_AUDIENCE_PROMPT
                + " For restricted memories, known_by must list only character names explicitly "
                "established as knowing the fact. Allowed kinds: scene_event, relationship_change, "
                "fact, goal, world_change, secret."
            ),
        },
        {"role": "user", "content": str(source_text)},
    ]
    settings = get_generation_settings(db, chat_id, session_id)
    settings.update(
        {
            "temperature": 0.1,
            "max_tokens": EPISODIC_MAX_OUTPUT_TOKENS,
            "reasoning_budget": utility_reasoning_for_session(db, chat_id, session_id),
        }
    )
    model = task_model_for_session(db, chat_id, session, "memory", app_settings=app_settings)
    candidates = generate_memory_response(
        provider_port.for_usage(chat_id, session_id, "memory").generate,
        "",
        model,
        messages,
        parser=parse_episodic_candidates,
        session_id=f"episodic:{chat_id}:{session_id}",
        settings=settings,
        source_valid=source_valid,
        repair_contract=(
            'An empty "memories" array must include a concise "no_memory_reason" explaining why the canonical '
            "source contains no qualifying durable fact. Re-check explicit promises, refusals, ownership, "
            "obligations, causal choices, goals, relationship changes, world changes, and secrets."
        ),
    )
    if source_ref is not None:
        with write_transaction(db):
            previous_id = int(db.execute("SELECT COALESCE(MAX(memory_id),0) FROM episodic_memories").fetchone()[0])
            ids = accept_source_facts(
                db,
                source_ref,
                [
                    MemoryFact(item.kind, item.importance, item.summary, item.visibility, item.known_by)
                    for item in candidates
                ],
                source_valid=source_valid,
            )
            if ids is None:
                return EpisodicExtractionResult("stale")
            return EpisodicExtractionResult("complete", sum(memory_id > previous_id for memory_id in ids), ids)
    inserted = 0
    with write_transaction(db):
        current_clock = load_narrative_clock(db, chat_id, session_id)
        if current_clock is None or (
            not source_valid()
            if source_valid is not None
            else any(current_clock[key] != source_clock[key] for key in identity_fields)
        ):
            return EpisodicExtractionResult("stale")
        for candidate in candidates:
            inserted += int(
                store_episodic_memory(
                    db,
                    chat_id,
                    session_id,
                    kind=candidate.kind,
                    importance=candidate.importance,
                    summary=candidate.summary,
                    source_start_rowid=source_start_rowid,
                    source_end_rowid=source_end_rowid,
                    visibility=candidate.visibility,
                    known_by=candidate.known_by,
                )
            )
    return EpisodicExtractionResult("complete", inserted)
