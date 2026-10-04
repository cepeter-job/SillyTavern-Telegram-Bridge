"""Canonical status panels owner."""

from __future__ import annotations

from pathlib import Path

from bridge.card_content import active_world_files, system_prompt_label
from bridge.context_diagnostics import context_diagnostics_snapshot
from bridge.expressions import expression_mode_key
from bridge.generation_settings import get_generation_settings
from bridge.group_service import GroupService
from bridge.humanizer_settings import humanizer_label
from bridge.language import response_language_label
from bridge.memory import get_session_summary
from bridge.memory_backend import memory_mode, memory_scope
from bridge.metadata import get_meta
from bridge.model_selection import task_model_for_session
from bridge.narrative_context import load_narrative_state, narrative_clock_is_current
from bridge.narrative_panels import FIELD_OPTIONS, PRESET_LABELS
from bridge.narrative_repository import load_narrative_clock
from bridge.narrative_settings import load_session_narrative_settings
from bridge.persona_sync import persona_name
from bridge.rag_query import rag_mode
from bridge.rag_repository import data_bank_documents
from bridge.settings import AppSettings


def _context_status_text(diagnostics: dict[str, object]) -> str:
    budget = int(diagnostics.get("budget_tokens") or 0)
    final = diagnostics.get("final_tokens")
    usage = diagnostics.get("usage_percent")
    if isinstance(final, int):
        input_text = f"{final:,} / {budget:,} input tokens"
        if isinstance(usage, int):
            input_text += f" ({usage}%)"
    else:
        input_text = f"not recorded / {budget:,} input tokens"
    source = str(diagnostics.get("source") or "global-fallback").replace("-", " ")
    compacted = diagnostics.get("compacted") is True
    details: list[str] = []
    dropped = int(diagnostics.get("dropped_history") or 0)
    if dropped:
        details.append(f"{dropped} history dropped")
    trimmed = diagnostics.get("trimmed_components")
    if isinstance(trimmed, list) and trimmed:
        details.append(f"{', '.join(str(item) for item in trimmed)} trimmed")
    compaction = "yes" if compacted else "no"
    if details:
        compaction += f" ({'; '.join(details)})"
    return (
        "📐 Context budget\n"
        f"• Input: {input_text}\n"
        f"• Window: {int(diagnostics.get('window_tokens') or 0):,} tokens ({source})\n"
        f"• Compaction: {compaction}"
    )


def status_text(
    db,
    chat_id,
    session,
    fields,
    current_model,
    current_persona,
    *,
    group_service: GroupService,
    app_settings: AppSettings,
):
    count = db.execute(
        "SELECT COUNT(*) FROM messages WHERE chat_id=? AND session_id=?",
        (chat_id, session["session_id"]),
    ).fetchone()[0]
    worlds = active_world_files(session["world_file"], app_settings=app_settings)
    world = ", ".join(Path(name).stem for name in worlds) if worlds else "off"
    persona = persona_name(current_persona, app_settings=app_settings) if current_persona else "off"
    note_state = "on" if session["author_note"] else "off"
    generation = get_generation_settings(db, chat_id, session["session_id"])
    summary, covered_until = get_session_summary(db, chat_id, session["session_id"])
    summary_state = f"on (through message {covered_until})" if summary else "off"
    rag_docs = data_bank_documents(db, chat_id)
    group = group_service.state(db, chat_id, session["session_id"])
    group_labels = group_service.member_labels(group["members"])
    group_state_text = f"{'on' if group['enabled'] else 'off'} ({', '.join(group_labels) if group_labels else 'none'})"
    expression_mode = get_meta(db, expression_mode_key(chat_id, session["session_id"]), "off")
    utility_model = task_model_for_session(db, chat_id, session, "utility", app_settings=app_settings)
    director_model = task_model_for_session(db, chat_id, session, "director", app_settings=app_settings)
    context_status = _context_status_text(
        context_diagnostics_snapshot(db, str(chat_id), session, app_settings=app_settings)
    )
    narrative = load_session_narrative_settings(db, chat_id, session["session_id"])
    narrative_state = load_narrative_state(db, chat_id, session["session_id"])
    narrative_current = narrative_clock_is_current(load_narrative_clock(db, chat_id, session["session_id"]))
    narrative_status = (
        f"Narrative: {PRESET_LABELS[narrative.preset]}\n"
        f"POV: {dict(FIELD_OPTIONS['pov_mode'])[narrative.pov_mode]}\n"
        f"Narrative state: {'current' if narrative_current else 'stale'}\n"
        f"Scene / thread: {narrative_state.active_scene_id[:60] or 'not established'} / "
        f"{narrative_state.active_thread_id[:60] or 'not established'}"
    )
    return (
        "📊 Session status\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🎭 Character: {fields.get('name') or 'unknown'}\n"
        f"🗂️ Session: {session.get('title') or session['session_id']} ({session['session_id']})\n"
        f"💬 Stored messages: {count}\n"
        f"🤖 Model: {current_model}\n"
        f"🛠️ Utility model: {utility_model}\n"
        f"🎬 Director model: {director_model}\n"
        f"🌐 Response language: {response_language_label(session.get('response_language') or 'auto')}\n"
        f"🖋️ Humanizer: {humanizer_label(session.get('humanizer'))}\n\n"
        f"{context_status}\n\n"
        f"{narrative_status}\n\n"
        "📚 Native context\n"
        f"• Persona: {persona}\n"
        f"• World Info: {world}\n"
        f"• System Prompt: {system_prompt_label(session.get('system_prompt'), app_settings=app_settings)}\n"
        f"• Author's Note: {note_state}\n"
        f"• Expressions: {expression_mode}\n\n"
        "🧠 Memory and state\n"
        f"• Summary: {summary_state}\n"
        f"• Hindsight: {memory_mode(db, chat_id)} ({memory_scope(db, chat_id)})\n"
        f"• Data Bank RAG: {rag_mode(db, chat_id)} ({len(rag_docs)} documents)\n"
        f"• Group chat: {group_state_text}\n\n"
        "⚙️ Generation\n"
        f"• Temperature: {generation['temperature']}\n"
        f"• Max tokens: {generation['max_tokens']}\n"
        f"• Top P: {generation['top_p']}"
    )
