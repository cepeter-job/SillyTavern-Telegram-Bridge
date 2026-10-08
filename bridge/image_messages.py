"""Canonical image messages owner."""

from __future__ import annotations

import base64
import sqlite3
import time

from bridge.action_turn import begin_action_for_job
from bridge.card_content import card_fields_from_file
from bridge.closed_session_guard import guard_story_mutation
from bridge.context_diagnostics import record_context_attempts
from bridge.conversation_lifecycle import START_REQUIRED, require_started
from bridge.delivery_progress import bind_committed_turn
from bridge.generation import build_chat_messages, finalize_generation_messages, render_session_response
from bridge.generation_settings import get_generation_settings
from bridge.group_director_service import GroupDirectorService
from bridge.group_service import GroupService
from bridge.light_novel_turn import begin_novel_turn
from bridge.limits import MAX_HISTORY_MESSAGES
from bridge.memory_service import MemoryService
from bridge.narrative_context import narrative_context_for_session
from bridge.npc_service import NpcService
from bridge.persona_service import PersonaService
from bridge.provider_port import ProviderPort
from bridge.rag_service import RagService
from bridge.response_delivery import send_reply
from bridge.response_variants import save_response_variant
from bridge.settings import AppSettings
from bridge.simulation_context import story_simulation_context
from bridge.sqlite_store import write_transaction
from bridge.telegram import send_text, send_typing


def process_image_message(
    db: sqlite3.Connection,
    token: str,
    api_key: str,
    session: dict,
    fields: dict,
    chat_id: str,
    caption: str,
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
    telegram_message_id: int | None = None,
    *,
    operation_id: int | None = None,
    group_service: GroupService,
    provider_port: ProviderPort,
    memory_service: MemoryService,
    npc_service: NpcService,
    persona_service: PersonaService,
    group_director_service: GroupDirectorService,
    app_settings: AppSettings,
    rag_service: RagService,
) -> None:
    guard_story_mutation(db, chat_id, session["session_id"])
    if not require_started(db, chat_id, session["session_id"]):
        send_text(token, chat_id, START_REQUIRED)
        return
    caption = caption.strip()[:12000] or "Please analyze this image in the context of the conversation."
    group_turn = group_service.current_speaker(db, chat_id, session, caption)
    group_context = ""
    if group_turn:
        fields = card_fields_from_file(group_turn[0], app_settings=app_settings)
        group_context = group_director_service.prompt_context(
            db,
            chat_id,
            session,
            group_turn[0],
        )
    image_data_uri = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    rows = db.execute(
        "SELECT role,content FROM messages WHERE chat_id=? AND session_id=? ORDER BY created_at,rowid",
        (chat_id, session["session_id"]),
    ).fetchall()
    history_rows = [(row[0], row[1]) for row in rows[-MAX_HISTORY_MESSAGES:]]
    rag_bundle = rag_service.bundle(db, chat_id, caption)
    memory_prompt = memory_service.prompt_context(
        db,
        chat_id,
        session,
        fields,
        caption,
    )
    memory_context = memory_prompt.recall
    episodic_context = memory_prompt.episodic
    session_summary = memory_prompt.summary
    npc_context = npc_service.context_for_prompt(
        db,
        chat_id,
        session,
        fields,
        caption,
        history_rows,
        through_rowid=memory_prompt.scope.through_rowid if memory_prompt.scope else None,
        memory_scope=memory_prompt.scope,
    )
    messages = build_chat_messages(
        session,
        fields,
        caption,
        history_rows,
        image_data_uri=image_data_uri,
        memory_context=memory_context,
        episodic_context=episodic_context,
        npc_context=npc_context,
        simulation_context=story_simulation_context(db, chat_id, session["session_id"], memory_prompt.scope),
        session_summary=session_summary,
        scene_context=memory_prompt.scene,
        memory_prompt=memory_prompt,
        defer_compaction=True,
        rag_context=rag_service.context_for_prompt(db, chat_id, caption, rag_bundle),
        group_context=group_context,
        persona_service=persona_service,
        narrative_context=narrative_context_for_session(db, chat_id, session["session_id"], "story"),
        app_settings=app_settings,
    )
    stored_text = f"[Image input] {caption}"
    action_turn = begin_action_for_job(
        db,
        chat_id,
        session,
        stored_text,
        operation_id,
        provider_port=provider_port,
        app_settings=app_settings,
        actor_id=str(session.get("_actor_id") or ""),
        group_turn=group_turn,
        telegram_message_id=telegram_message_id,
    )
    messages = action_turn.messages(messages)
    novel_turn = begin_novel_turn(db, chat_id, session, "image", telegram_message_id)
    if novel_turn:
        messages = novel_turn.messages(messages, session.get("response_language") or "auto")
    generation_settings = get_generation_settings(db, chat_id, session["session_id"])
    messages = finalize_generation_messages(
        db,
        chat_id,
        session,
        messages,
        generation_settings,
        app_settings=app_settings,
    )
    send_typing(token, chat_id)
    with record_context_attempts(db, chat_id, session["session_id"]) as observe_context:
        reply = (
            provider_port.for_usage(chat_id, session["session_id"], "image")
            .with_context_observer(observe_context)
            .generate(
                api_key,
                session["model_id"],
                messages,
                session_id=f"telegram:{chat_id}:{session['session_id']}",
                settings=generation_settings,
            )
        )
    if novel_turn:
        reply = novel_turn.extract(reply)
    reply += rag_service.citation_footer(db, chat_id, caption, rag_bundle)
    reply = render_session_response(
        api_key,
        session,
        reply,
        chat_id,
        generation_settings,
        provider_port=provider_port,
        novel_turn=novel_turn,
    )
    stored_reply = (
        reply
        if group_turn and group_turn[1].get("mode") == "autonomous"
        else (f"{fields['name']}: {reply}" if group_turn else reply)
    )
    with write_transaction(db):
        action_turn.validate(db)
        user_cursor = db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,telegram_message_id,created_at) VALUES(?,?,?,?,?,?)",
            (
                chat_id,
                session["session_id"],
                "user",
                stored_text,
                str(telegram_message_id) if telegram_message_id is not None else None,
                time.time(),
            ),
        )
        assistant_cursor = db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            (chat_id, session["session_id"], "assistant", stored_reply, time.time()),
        )
        action_turn.bind(db, int(user_cursor.lastrowid))
        assistant_rowid = assistant_cursor.lastrowid
        bind_committed_turn(db, operation_id, int(user_cursor.lastrowid), int(assistant_rowid), stored_reply)
        if novel_turn:
            novel_turn.commit(db, int(assistant_rowid), stored_reply)
        save_response_variant(db, chat_id, session["session_id"], stored_text, stored_reply)
        if group_turn:
            group_service.advance_turn(db, chat_id, session["session_id"])
    memory_service.retain(db, chat_id, session, fields)
    send_reply(
        token,
        chat_id,
        stored_reply,
        db,
        session["session_id"],
        assistant_rowid,
        expected_job_id=operation_id,
        app_settings=app_settings,
    )
