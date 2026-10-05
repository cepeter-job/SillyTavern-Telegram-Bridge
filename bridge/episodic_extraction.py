"""Durable episodic-memory extraction from stable conversation segments."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from bridge.episodic_memory import store_episodic_memory
from bridge.generation_settings import get_generation_settings
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


def _unfence(value: str) -> str:
    text = str(value or "").strip()
    fence = chr(96) * 3
    if not (text.startswith(fence) and text.endswith(fence)):
        return text
    inner = text[len(fence) : -len(fence)].strip()
    if inner.casefold().startswith("json"):
        inner = inner[4:].lstrip()
    return inner


def parse_episodic_candidates(source: str) -> list[EpisodicCandidate]:
    try:
        payload = json.loads(_unfence(source))
    except (TypeError, ValueError) as exc:
        raise ValueError("episodic memory response is not valid JSON") from exc
    if isinstance(payload, dict):
        payload = payload.get("memories")
    if not isinstance(payload, list):
        raise ValueError("episodic memory response must be a JSON array")
    result: list[EpisodicCandidate] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip().casefold()
        summary = str(item.get("summary") or "").strip()
        raw_visibility = item.get("visibility")
        visibility = (
            "restricted"
            if raw_visibility is None and kind == "secret"
            else str(raw_visibility or "shared").strip().casefold()
        )
        known_by_raw = item.get("known_by") or []
        known_by = (
            tuple(name for name in (" ".join(str(value or "").split()).strip() for value in known_by_raw) if name)
            if isinstance(known_by_raw, list)
            else ()
        )
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
    return result


@dataclass(frozen=True)
class EpisodicExtractionResult:
    status: str
    inserted: int = 0


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
) -> EpisodicExtractionResult:
    if not str(source_text or "").strip():
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
                "Return only a JSON array with objects containing kind, importance (0 to 1), "
                "summary, visibility, and known_by. visibility must be shared or restricted. "
                "For restricted memories, known_by must list only character names explicitly "
                "established as knowing the fact. Allowed kinds: scene_event, relationship_change, "
                "fact, goal, world_change, secret."
            ),
        },
        {"role": "user", "content": str(source_text)[:50000]},
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
    response = provider_port.for_usage(chat_id, session_id, "memory").generate(
        "",
        model,
        messages,
        session_id=f"episodic:{chat_id}:{session_id}",
        settings=settings,
    )
    candidates = parse_episodic_candidates(response)
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


def extract_episodic_memories(*args, **kwargs) -> int:
    """Compatibility integer view; durable workers consume the explicit result."""
    return extract_episodic_memories_result(*args, **kwargs).inserted
