from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial as _partial

from bridge.background import submit_background
from bridge.delivery_port import DeliveryPort
from bridge.episodic_extraction import extract_episodic_memories
from bridge.extension_registry import apply_summary_context_hooks as _apply_summary_context_hooks
from bridge.extension_registry import run_post_retain_hooks as _run_post_retain_hooks
from bridge.extension_registry import run_summary_clear_hooks as _run_summary_clear_hooks
from bridge.generation_settings import get_generation_settings
from bridge.hindsight_integrity import HindsightStaleGuard as _HindsightStaleGuard
from bridge.limits import (
    SUMMARY_MAX_CHARS,
    SUMMARY_MAX_OUTPUT_TOKENS,
    SUMMARY_RECENT_MESSAGES,
    SUMMARY_TRIGGER_MESSAGES,
    SUMMARY_UPDATE_INTERVAL,
)
from bridge.memory_backend import (
    _memory_hindsight_conversation_snapshot,
    _memory_hindsight_epoch,
    _memory_hindsight_session_exists,
    _prepare_hindsight_purge_state,
    _purge_hindsight_session_backend,
    _retain_session_memory_backend,
    _write_hindsight_successful_purge_state,
    hindsight_bank_id,
    hindsight_session_lock,
    memory_mode,
    memory_scope,
    recall_memory_results,
)
from bridge.memory_backend import _retain_with_client as _retain_with_client
from bridge.memory_backend import hindsight_client as hindsight_client
from bridge.memory_backend import hindsight_conversation_document_id as hindsight_conversation_document_id
from bridge.memory_backend import hindsight_explicit_document_id as hindsight_explicit_document_id
from bridge.memory_backend import hindsight_session_prefix as hindsight_session_prefix
from bridge.memory_backend import hindsight_tags as hindsight_tags
from bridge.memory_backend import memory_recall_filter as memory_recall_filter
from bridge.memory_backend import recall_memory_context as recall_memory_context
from bridge.memory_backend import remember_fact as remember_fact
from bridge.memory_store import enqueue_memory
from bridge.metadata import set_meta
from bridge.model_selection import task_model_for_session, utility_reasoning_for_session
from bridge.narrative_repository import load_narrative_clock
from bridge.persona_service import PersonaService
from bridge.provider_port import ProviderPort
from bridge.settings import AppSettings
from bridge.sqlite_store import db_connect, write_transaction


def _make_hindsight_stale_guard(
    *,
    app_settings: AppSettings,
    persona_service: PersonaService | None = None,
    delivery_port: DeliveryPort | None = None,
):
    return _HindsightStaleGuard(
        open_db=lambda: db_connect(app_settings=app_settings),
        session_lock=lambda chat_id, session_id: hindsight_session_lock(
            chat_id,
            session_id,
        ),
        memory_enabled=lambda db, chat_id: memory_mode(db, chat_id) == "on",
        submit_background=(
            lambda name, fn, *args, **kwargs: submit_background(
                name,
                fn,
                *args,
                **kwargs,
            )
        ),
        session_exists=_memory_hindsight_session_exists,
        read_epoch=_memory_hindsight_epoch,
        snapshot=_memory_hindsight_conversation_snapshot,
        retain_backend=_partial(_retain_session_memory_backend, app_settings=app_settings),
        purge_backend=_partial(_purge_hindsight_session_backend, app_settings=app_settings),
        write_successful_purge_state=(_write_hindsight_successful_purge_state),
        invalidate_before_purge=_prepare_hindsight_purge_state,
        run_post_retain_hooks=_partial(
            _run_post_retain_hooks,
            app_settings=app_settings,
            persona_service=persona_service,
            delivery_port=delivery_port,
        ),
    )


def retain_session_memory(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict[str, str],
    fields: dict[str, str],
    *,
    provider_port: ProviderPort,
    app_settings: AppSettings,
    persona_service: PersonaService | None = None,
    delivery_port: DeliveryPort | None = None,
) -> None:
    # Transcript triggers already captured work in the accepted write transaction.
    # Explicit requests also recover work when a caller retains existing rows.
    enqueue_memory(db, chat_id, session["session_id"], "hindsight")
    _run_post_retain_hooks(
        db,
        chat_id,
        session,
        fields,
        provider_port,
        app_settings=app_settings,
        persona_service=persona_service,
        delivery_port=delivery_port,
    )


def purge_hindsight_session(db: sqlite3.Connection, chat_id: str, session_id: str, *, app_settings: AppSettings) -> int:
    return _make_hindsight_stale_guard(app_settings=app_settings).purge(
        db,
        chat_id,
        session_id,
    )


def handle_memory_command(
    db: sqlite3.Connection,
    token: str,
    chat_id: str,
    session: dict[str, str],
    fields: dict[str, str],
    command_text: str,
    *,
    send_text_fn: Callable[[str, str, str], object],
    app_settings: AppSettings,
) -> None:
    parts = command_text.split(None, 2)
    argument = parts[1].casefold() if len(parts) > 1 else "status"
    if argument == "scope":
        requested_scope = parts[2].casefold() if len(parts) > 2 else ""
        if requested_scope != "session":
            send_text_fn(
                token, chat_id, "Hindsight recall is fixed to the active session; broader scopes are disabled."
            )
            return
        send_text_fn(token, chat_id, "Hindsight memory scope is already fixed to session.")
        return
    if argument in {"status", "on", "off"}:
        if argument in {"on", "off"}:
            with hindsight_session_lock(chat_id, session["session_id"]):
                set_meta(db, f"memory_mode:{chat_id}", argument)
                if argument == "on":
                    with write_transaction(db):
                        db.execute("UPDATE memory_jobs SET next_attempt_at=0 WHERE chat_id=?", (chat_id,))
        status = memory_mode(db, chat_id)
        send_text_fn(
            token,
            chat_id,
            (
                "Hindsight memory: "
                f"""{status}"""
                "\nScope: "
                f"""{memory_scope(db, chat_id)}"""
                "\nBank: "
                f"""{hindsight_bank_id(chat_id)}"""
                "\nRecall is hard-filtered to the active session; character tags are "
                "provenance only."
            ),
        )
        return
    if argument == "search":
        query = parts[2].strip() if len(parts) > 2 else ""
        if not query:
            send_text_fn(token, chat_id, "Use /memory search <query>.")
            return
        results = recall_memory_results(
            db, chat_id, session, query, fields["name"], max_tokens=1600, app_settings=app_settings
        )
        lines = [str(getattr(result, "text", "") or "").strip() for result in results]
        lines = [f"- {line}" for line in lines if line][:5]
        send_text_fn(
            token, chat_id, "Recalled memories:\n" + ("\n".join(lines) if lines else "No matching memories found.")
        )
        return
    send_text_fn(token, chat_id, "Use /memory on, /memory off, /memory status, or /memory search <query>.")


def get_session_summary(db: sqlite3.Connection, chat_id: str, session_id: str) -> tuple[str, int]:
    row = db.execute(
        "SELECT summary,covered_until_rowid FROM session_summaries WHERE chat_id=? AND session_id=?",
        (chat_id, session_id),
    ).fetchone()
    return (str(row[0]), int(row[1])) if row else ("", 0)


def clear_session_summary(db: sqlite3.Connection, chat_id: str, session_id: str) -> None:
    with write_transaction(db):
        db.execute("DELETE FROM session_summaries WHERE chat_id=? AND session_id=?", (chat_id, session_id))
        _run_summary_clear_hooks(db, chat_id, session_id)


def transcript_for_summary(rows: list[tuple[int, str, str, float]]) -> str:
    return "\n".join(
        f"{role.upper()} ({time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(created_at))}): {content}"
        for _, role, content, created_at in rows
    )


@dataclass(frozen=True)
class SessionSummaryResult:
    summary: str
    covered_until_rowid: int
    complete: bool


def _summary_generation_snapshot(db: sqlite3.Connection, chat_id: str, session_id: str) -> tuple | None:
    """Capture story identity and the accepted summary without copying the transcript."""
    clock = load_narrative_clock(db, chat_id, session_id)
    if clock is None:
        return None
    summary = db.execute(
        "SELECT summary,covered_until_rowid,updated_at FROM session_summaries WHERE chat_id=? AND session_id=?",
        (chat_id, session_id),
    ).fetchone()
    return clock["session_created_at"], clock["history_revision"], clock["latest_rowid"], summary


def _current_summary_result(db: sqlite3.Connection, chat_id: str, session_id: str) -> SessionSummaryResult:
    summary, covered = get_session_summary(db, chat_id, session_id)
    return SessionSummaryResult(summary, covered, False)


def generate_session_summary(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict[str, str],
    force: bool = False,
    *,
    provider_port: ProviderPort,
    app_settings: AppSettings,
) -> str:
    return generate_session_summary_result(
        db, chat_id, session, force=force, provider_port=provider_port, app_settings=app_settings
    ).summary


def generate_session_summary_result(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict[str, str],
    force: bool = False,
    *,
    provider_port: ProviderPort,
    app_settings: AppSettings,
    durable: bool = False,
    max_segments: int | None = None,
) -> SessionSummaryResult:
    source_snapshot = _summary_generation_snapshot(db, chat_id, session["session_id"])
    rows = db.execute(
        "SELECT rowid,role,content,created_at FROM messages WHERE chat_id=? AND session_id=? ORDER BY created_at,rowid",
        (chat_id, session["session_id"]),
    ).fetchall()
    if durable:
        rows.sort(key=lambda row: int(row[0]))
    if not rows:
        return SessionSummaryResult("", 0, True)
    existing, covered_until = get_session_summary(db, chat_id, session["session_id"])
    stable_rows = rows if force else rows[:-SUMMARY_RECENT_MESSAGES]
    if not stable_rows:
        return SessionSummaryResult(existing, covered_until, True)
    if db.in_transaction:
        raise RuntimeError("Summary generation cannot run inside a write transaction")
    target_rowid = int(stable_rows[-1][0])
    if not force and existing and target_rowid <= covered_until:
        return SessionSummaryResult(existing, covered_until, True)
    pending = stable_rows if force and not durable else [row for row in stable_rows if int(row[0]) > covered_until]
    if not pending:
        return SessionSummaryResult(existing, covered_until, durable and target_rowid <= covered_until)
    if not force and existing and len(pending) < SUMMARY_UPDATE_INTERVAL:
        return SessionSummaryResult(existing, covered_until, False)
    settings = get_generation_settings(db, chat_id, session["session_id"])
    settings.update(
        {
            "temperature": 0.2,
            "max_tokens": SUMMARY_MAX_OUTPUT_TOKENS,
            "reasoning_budget": utility_reasoning_for_session(db, chat_id, session["session_id"]),
        }
    )
    summary = existing
    completed_covered = covered_until
    previous = "" if force and not durable else existing
    processed_segments = 0
    while pending:
        if max_segments is not None and processed_segments >= max_segments:
            return SessionSummaryResult(summary, completed_covered, False)
        if (
            source_snapshot is None
            or _summary_generation_snapshot(db, chat_id, session["session_id"]) != source_snapshot
        ):
            return _current_summary_result(db, chat_id, session["session_id"])
        header = (("Previous summary:\n" + previous + "\n\n") if previous else "") + "New transcript segment:\n"
        segment = []
        rendered = []
        length = len(header)
        for row in pending:
            text = transcript_for_summary([row])
            needed = len(text) + bool(rendered)
            if length + needed > 50000:
                break
            segment.append(row)
            rendered.append(text)
            length += needed
        if not segment:
            logging.warning(
                "Summary source row %s exceeds input budget for %s/%s", pending[0][0], chat_id, session["session_id"]
            )
            return SessionSummaryResult(summary, completed_covered, False)
        source = header + "\n".join(rendered)
        prompt_prefix = (
            "Create a fresh summary from the transcript segment below."
            if force and not previous
            else "Update the previous summary using the new transcript segment."
        )
        summary_messages = [
            {
                "role": "system",
                "content": (
                    "You compress a fictional roleplay chat for future continuity. Preserve "
                    "current location, characters, relationships, established facts, goals, "
                    "unresolved hooks, tone, and the latest scene state. Do not invent facts, "
                    "do not give advice, and do not include meta commentary. Output only a "
                    "concise continuity summary."
                ),
            },
            {"role": "user", "content": f"{prompt_prefix}\n\n{source}"},
        ]
        try:
            summary_model = task_model_for_session(db, chat_id, session, "summary", app_settings=app_settings)
            produced = (
                provider_port.for_usage(chat_id, session["session_id"], "summary")
                .generate(
                    "",
                    summary_model,
                    summary_messages,
                    session_id=f"summary:{chat_id}:{session['session_id']}",
                    settings=settings,
                )
                .strip()[:SUMMARY_MAX_CHARS]
            )
        except Exception:
            logging.warning(
                "Session summary generation failed for %s/%s", chat_id, session["session_id"], exc_info=True
            )
            return _current_summary_result(db, chat_id, session["session_id"])
        if not produced:
            return _current_summary_result(db, chat_id, session["session_id"])
        completed_rowid = int(segment[-1][0])
        with write_transaction(db):
            if _summary_generation_snapshot(db, chat_id, session["session_id"]) != source_snapshot:
                return _current_summary_result(db, chat_id, session["session_id"])
            db.execute(
                "INSERT OR REPLACE INTO session_summaries"
                "(chat_id,session_id,summary,covered_until_rowid,updated_at) VALUES(?,?,?,?,?)",
                (chat_id, session["session_id"], produced, completed_rowid, time.time()),
            )
            source_snapshot = _summary_generation_snapshot(db, chat_id, session["session_id"])
        summary = previous = produced
        completed_covered = completed_rowid
        if not force and not durable:
            try:
                extract_episodic_memories(
                    db,
                    chat_id,
                    session,
                    source_text="\n".join(rendered),
                    source_start_rowid=int(segment[0][0]),
                    source_end_rowid=completed_rowid,
                    expected_source=(source_snapshot[0], source_snapshot[1], source_snapshot[2]),
                    provider_port=provider_port,
                    app_settings=app_settings,
                )
            except Exception:
                logging.warning(
                    "Episodic memory extraction failed for %s/%s", chat_id, session["session_id"], exc_info=True
                )
        pending = pending[len(segment) :]
        processed_segments += 1
    if _summary_generation_snapshot(db, chat_id, session["session_id"]) != source_snapshot:
        return _current_summary_result(db, chat_id, session["session_id"])
    return SessionSummaryResult(summary, completed_covered, True)


def session_summary_for_prompt(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict[str, str],
    *,
    provider_port: ProviderPort,
    app_settings: AppSettings,
) -> str:
    summary, _covered_until = get_session_summary(db, chat_id, session["session_id"])
    count = db.execute(
        "SELECT COUNT(*) FROM messages WHERE chat_id=? AND session_id=?", (chat_id, session["session_id"])
    ).fetchone()[0]
    if count >= SUMMARY_TRIGGER_MESSAGES:
        summary = generate_session_summary(db, chat_id, session, provider_port=provider_port, app_settings=app_settings)
    return _apply_summary_context_hooks(summary, db, chat_id, session)
