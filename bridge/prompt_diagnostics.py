"""Canonical prompt diagnostics owner."""

from __future__ import annotations

import sqlite3

from bridge.context_compaction import context_history_candidate_limit
from bridge.context_diagnostics import context_diagnostics_snapshot
from bridge.group_service import GroupService
from bridge.memory_backend import memory_mode, memory_scope
from bridge.memory_service import MemoryService
from bridge.rag_query import rag_mode
from bridge.rag_repository import data_bank_documents
from bridge.settings import AppSettings


def prompt_diagnostics(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict[str, str],
    fields: dict[str, str],
    *,
    group_service: GroupService,
    memory_service: MemoryService,
    app_settings: AppSettings,
) -> str:
    message_count = db.execute(
        "SELECT COUNT(*) FROM messages WHERE chat_id=? AND session_id=?", (chat_id, session["session_id"])
    ).fetchone()[0]
    summary, covered_until = memory_service.summary_status(db, chat_id, session["session_id"])
    docs = data_bank_documents(db, chat_id)
    group = group_service.state(db, chat_id, session["session_id"])
    context = context_diagnostics_snapshot(db, chat_id, session, app_settings=app_settings)
    last_prompt = (
        "not recorded yet"
        if context["final_tokens"] is None
        else f"{context['final_tokens']} / {context['budget_tokens']} estimated tokens"
    )
    compaction = (
        f"dropped={context['dropped_history']}, memory={context['memory_trimmed']}, "
        f"rag={context['rag_trimmed']}, npc={context['npc_trimmed']}, summary={context['summary_trimmed']}"
    )
    return (
        "Prompt inspector\nCharacter: "
        f"{fields['name']}"
        "\nMessages: "
        f"{message_count}"
        "\nContext window: "
        f"{context['window_tokens']}"
        " tokens ("
        f"{context['source']}"
        ")\nOutput reserve: "
        f"{context['output_reserve_tokens']}"
        " tokens\nSafety margin: "
        f"{context['safety_margin_tokens']}"
        " tokens\nContext input budget: ~"
        f"{context['budget_tokens']}"
        " tokens\nLast assembled prompt: "
        f"{last_prompt}"
        "\nLast compaction: "
        f"{compaction}"
        "\nHistory candidates: "
        f"{context_history_candidate_limit(app_settings=app_settings)}"
        " messages\nSession summary: "
        f"{len(summary)}"
        " chars (through row "
        f"{covered_until}"
        ")\nHindsight: "
        f"{memory_mode(db, chat_id)}"
        " / "
        f"{memory_scope(db, chat_id)}"
        "\nData Bank: "
        f"{rag_mode(db, chat_id)}"
        " / "
        f"{len(docs)}"
        " documents\nGroup: "
        f"{('on' if group['enabled'] else 'off')}"
        "\nMacro support: char, user, random, pick, time, date, weekday\nWorld Info recursion: maximum 3 passes"
    )
