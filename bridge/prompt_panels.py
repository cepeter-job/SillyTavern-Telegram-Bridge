"""Canonical prompt panels owner."""

from __future__ import annotations

from bridge.cards import send_panel_message
from bridge.context_compaction import context_history_candidate_limit
from bridge.context_diagnostics import context_diagnostics_snapshot
from bridge.group_service import GroupService
from bridge.memory import get_session_summary
from bridge.memory_backend import memory_mode, memory_scope
from bridge.prompt_diagnostics import prompt_diagnostics
from bridge.rag_query import rag_mode
from bridge.rag_repository import data_bank_documents
from bridge.settings import AppSettings


def prompt_panel_text(
    db,
    chat_id,
    session,
    fields,
    section="overview",
    *,
    group_service: GroupService,
    memory_service,
    app_settings: AppSettings,
):
    if section == "budget":
        context = context_diagnostics_snapshot(db, chat_id, session, app_settings=app_settings)
        last_prompt = (
            "not recorded yet"
            if context["final_tokens"] is None
            else f"{context['final_tokens']} / {context['budget_tokens']} estimated tokens"
        )
        budget_label = "Last request input budget" if context["final_tokens"] is not None else "Effective input budget"
        return (
            "Prompt budget\n"
            f"Context window: {context['window_tokens']} tokens ({context['source']})\n"
            f"Output reserve: {context['output_reserve_tokens']} tokens\n"
            f"Safety margin: {context['safety_margin_tokens']} tokens\n"
            f"Configured input cap: {context['configured_input_cap_tokens']} tokens\n"
            f"Request input cap: {context['input_cap_tokens']} tokens\n"
            f"{budget_label}: ~{context['budget_tokens']} tokens "
            f"({'input cap' if context['input_budget_limiter'] == 'input-cap' else 'model window'})\n"
            f"Last assembled prompt: {last_prompt}\n"
            f"History dropped last time: {context['dropped_history']}\n"
            f"Trimmed: memory={context['memory_trimmed']}, rag={context['rag_trimmed']}, "
            f"npc={context['npc_trimmed']}, summary={context['summary_trimmed']}\n"
            f"History candidates: {context_history_candidate_limit(app_settings=app_settings)} messages\n"
            f"Session summary: {len(get_session_summary(db, chat_id, session['session_id'])[0])} chars"
        )
    if section == "memory":
        docs = data_bank_documents(db, chat_id)
        return (
            f"Prompt memory and retrieval\nHindsight: {memory_mode(db, chat_id)} / {memory_scope(db, chat_id)}\n"
            f"Data Bank: {rag_mode(db, chat_id)} / {len(docs)} documents"
        )
    if section == "group":
        group = group_service.state(db, chat_id, session["session_id"])
        return (
            "Prompt group context\nEnabled: "
            f"{('on' if group['enabled'] else 'off')}"
            "\nMode: "
            f"{group['mode']}"
            "\nMembers: "
            f"{len(group['members'])}"
        )
    return prompt_diagnostics(
        db,
        chat_id,
        session,
        fields,
        group_service=group_service,
        memory_service=memory_service,
        app_settings=app_settings,
    )


def send_prompt_menu(
    token,
    chat_id,
    db,
    session,
    fields,
    message_id=None,
    section="overview",
    *,
    group_service: GroupService,
    memory_service,
    request_context,
):
    labels = {
        "overview": "Prompt inspector",
        "budget": "Prompt budget",
        "memory": "Memory and retrieval",
        "group": "Group context",
    }
    markup = {
        "inline_keyboard": [
            [
                {"text": "📏 Budget", "callback_data": "prompt:budget"},
                {"text": "🧠 Memory / RAG", "callback_data": "prompt:memory"},
            ],
            [{"text": "👥 Group", "callback_data": "prompt:group"}],
            [
                {"text": "⬅️ Status", "callback_data": "prompt:status"},
                {"text": "❌ Close", "callback_data": "prompt:close"},
            ],
        ]
    }
    send_panel_message(
        token,
        chat_id,
        labels.get(section, labels["overview"])
        + "\n\n"
        + prompt_panel_text(
            db,
            chat_id,
            session,
            fields,
            section,
            group_service=group_service,
            memory_service=memory_service,
            app_settings=request_context.app_settings,
        ),
        markup,
        message_id,
        request_context=request_context,
    )
