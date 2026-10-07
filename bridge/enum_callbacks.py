"""Canonical enum callbacks owner."""

from __future__ import annotations

import json
import sqlite3
import time

from bridge.callback_tokens import resolve_dynamic_callback_token
from bridge.callbacks import close_panel_message, discard_panel_binding
from bridge.cards import send_panel_message
from bridge.config import GENERATION_DEFAULTS, REASONING_LEVELS
from bridge.databank_panels import (
    send_databank_menu,
    send_databank_remove_confirm,
    send_databank_remove_menu,
    send_databank_versions_menu,
)
from bridge.generation_settings import update_generation_settings
from bridge.grounded_user_settings import grounded_user_enabled, normalize_grounded_user, session_grounded_user
from bridge.humanizer_settings import humanizer_enabled, normalize_humanizer, session_humanizer
from bridge.input_flow_service import InputFlowService
from bridge.language import normalize_stt_language
from bridge.limits import PENDING_SETTINGS_TTL_SECONDS
from bridge.memory_panels import send_memory_menu
from bridge.metadata import set_meta
from bridge.preset_actions import apply_preset_action
from bridge.preset_panels import send_preset_delete_confirm, send_preset_delete_menu, send_preset_menu
from bridge.rag_service import RagService
from bridge.reset_panel import reset_confirmation_request
from bridge.session_core import update_session
from bridge.settings_panels import send_settings_menu, send_stream_menu
from bridge.sqlite_store import write_transaction
from bridge.telegram import send_text
from bridge.voice_panels import send_stt_language_menu, send_stt_model_menu, send_voice_input_menu, send_voice_menu


def _handle_close(db: sqlite3.Connection, token: str, chat_id: str, message: dict, message_id) -> None:
    discard_panel_binding(db, chat_id, message_id)
    close_panel_message(db, token, chat_id, {"message": message})
    return


def _handle_stscript_reset(
    db: sqlite3.Connection, token: str, chat_id: str, message: dict, request_context, message_id
) -> None:
    discard_panel_binding(db, chat_id, message_id)
    close_panel_message(db, token, chat_id, {"message": message})
    _method, payload = reset_confirmation_request(chat_id)
    send_panel_message(
        token,
        chat_id,
        payload["text"],
        payload["reply_markup"],
        request_context=request_context,
    )
    return


def _handle_settings_input(
    db: sqlite3.Connection, token: str, chat_id: str, session: dict[str, str], data: str, message: dict, message_id
) -> None:
    key = data.rsplit(":", 1)[1]
    prompts = {
        "temperature": "Send temperature (0–2).",
        "max_tokens": "Send max_tokens (1–16000).",
        "top_p": "Send top_p (0–1).",
        "frequency_penalty": "Send frequency_penalty (-2–2).",
        "presence_penalty": "Send presence_penalty (-2–2).",
        "reasoning_budget": "Send reasoning budget (0–32000).",
        "stop_sequences": "Send stop sequences as text; separate multiple stops with newlines.",
    }
    if key in prompts:
        pending_setting = {
            "key": key,
            "session_id": session["session_id"],
            "expires_at": time.time() + PENDING_SETTINGS_TTL_SECONDS,
        }
        set_meta(db, f"settings_input:{chat_id}", json.dumps(pending_setting))
        discard_panel_binding(db, chat_id, message_id)
        close_panel_message(db, token, chat_id, {"message": message})
        pending_setting["prompt_message_ids"] = send_text(
            token, chat_id, prompts[key] + " Send /cancel to leave it unchanged."
        )
        set_meta(db, f"settings_input:{chat_id}", json.dumps(pending_setting))
    return


def _handle_settings_reset(
    db: sqlite3.Connection, token: str, chat_id: str, session: dict[str, str], request_context, message_id
) -> None:
    with write_transaction(db):
        update_generation_settings(db, chat_id, session["session_id"], **GENERATION_DEFAULTS)
        update_session(db, chat_id, session["session_id"], humanizer="off", grounded_user="off")
    send_settings_menu(token, chat_id, db, session["session_id"], message_id, request_context=request_context)
    return


def _handle_settings_reasoning(
    db: sqlite3.Connection, token: str, chat_id: str, session: dict[str, str], data: str, request_context, message_id
) -> None:
    label = data.rsplit(":", 1)[1]
    budgets = REASONING_LEVELS
    if label in budgets:
        update_generation_settings(db, chat_id, session["session_id"], reasoning_budget=budgets[label])
    send_settings_menu(token, chat_id, db, session["session_id"], message_id, request_context=request_context)
    return


def _handle_humanizer(
    db: sqlite3.Connection, token: str, chat_id: str, session: dict[str, str], data: str, request_context, message_id
) -> None:
    value = data.rsplit(":", 1)[1]
    if value == "toggle":
        current = humanizer_enabled(session_humanizer(db, chat_id, session["session_id"]))
        value = "off" if current else "on"
    try:
        update_session(db, chat_id, session["session_id"], humanizer=normalize_humanizer(value))
    except ValueError:
        send_text(token, chat_id, "Invalid Humanizer choice.")
        return
    send_settings_menu(token, chat_id, db, session["session_id"], message_id, request_context=request_context)
    return


def _handle_grounded(
    db: sqlite3.Connection, token: str, chat_id: str, session: dict[str, str], data: str, request_context, message_id
) -> None:
    value = data.rsplit(":", 1)[1]
    if value == "toggle":
        current = grounded_user_enabled(session_grounded_user(db, chat_id, session["session_id"]))
        value = "off" if current else "on"
    try:
        update_session(db, chat_id, session["session_id"], grounded_user=normalize_grounded_user(value))
    except ValueError:
        send_text(token, chat_id, "Invalid I am not MC mode choice.")
        return
    send_settings_menu(token, chat_id, db, session["session_id"], message_id, request_context=request_context)
    return


def _handle_stream(db: sqlite3.Connection, token: str, chat_id: str, request_context, message_id, parts) -> None:
    value = parts[2]
    if value in {"on", "off"}:
        set_meta(db, f"stream_mode:{chat_id}", value)
    send_stream_menu(token, chat_id, db, message_id, request_context=request_context)


def _handle_voice(db: sqlite3.Connection, token: str, chat_id: str, request_context, message_id, parts) -> None:
    value = parts[2]
    if value in {"on", "off"}:
        set_meta(db, f"voice_mode:{chat_id}", "tts" if value == "on" else "off")
    send_voice_menu(token, chat_id, db, message_id, request_context=request_context)


def _handle_stt_language(db: sqlite3.Connection, token: str, chat_id: str, request_context, message_id) -> None:
    send_stt_language_menu(token, chat_id, db, message_id, request_context=request_context)


def _handle_stt_language_input(
    db: sqlite3.Connection, token: str, chat_id: str, session: dict[str, str], message: dict, message_id
) -> None:
    pending_stt = {"session_id": session["session_id"], "expires_at": time.time() + PENDING_SETTINGS_TTL_SECONDS}
    set_meta(db, f"stt_language_input:{chat_id}", json.dumps(pending_stt))
    discard_panel_binding(db, chat_id, message_id)
    close_panel_message(db, token, chat_id, {"message": message})
    pending_stt["prompt_message_ids"] = send_text(
        token, chat_id, "Send a 2–8 letter STT language code such as id, en, or ja. Send /cancel to cancel."
    )
    set_meta(db, f"stt_language_input:{chat_id}", json.dumps(pending_stt))


def _handle_sttlanguagepage(
    db: sqlite3.Connection, token: str, chat_id: str, request_context, message_id, parts
) -> None:
    send_stt_language_menu(token, chat_id, db, message_id, int(parts[2]), request_context=request_context)


def _handle_sttlanguage(db: sqlite3.Connection, token: str, chat_id: str, request_context, message_id, parts) -> None:
    try:
        value = normalize_stt_language(parts[2])
    except ValueError:
        send_text(token, chat_id, "Invalid STT language choice.")
    else:
        set_meta(db, f"stt_language:{chat_id}", value)
        send_voice_input_menu(token, chat_id, db, message_id, request_context=request_context)


def _handle_stt_model(db: sqlite3.Connection, token: str, chat_id: str, request_context, message_id) -> None:
    send_stt_model_menu(token, chat_id, db, message_id, request_context=request_context)


def _handle_stt_back(db: sqlite3.Connection, token: str, chat_id: str, request_context, message_id) -> None:
    send_voice_input_menu(token, chat_id, db, message_id, request_context=request_context)


def _handle_stt(db: sqlite3.Connection, token: str, chat_id: str, request_context, message_id, parts) -> None:
    value = parts[2]
    if value in {"on", "off"}:
        set_meta(db, f"stt_mode:{chat_id}", value)
    send_voice_input_menu(token, chat_id, db, message_id, request_context=request_context)


def _handle_sttmodel(db: sqlite3.Connection, token: str, chat_id: str, request_context, message_id, parts) -> None:
    value = parts[2]
    if value in {"tiny", "base", "small"}:
        set_meta(db, f"stt_model:{chat_id}", value)
    send_voice_input_menu(token, chat_id, db, message_id, request_context=request_context)


def _handle_memory_search(
    db: sqlite3.Connection,
    token: str,
    chat_id: str,
    session: dict[str, str],
    message: dict,
    input_flow_service: InputFlowService,
    request_context,
) -> None:
    input_flow_service.start_text_action(
        db,
        token,
        chat_id,
        session["session_id"],
        "memory_search",
        "Send a query to search Hindsight memory for the active session.",
        {"message": message},
        request_context=request_context,
    )


def _handle_memory_scope(db: sqlite3.Connection, token: str, chat_id: str, request_context, message_id) -> None:
    send_memory_menu(token, chat_id, db, message_id, request_context=request_context)


def _handle_memory(db: sqlite3.Connection, token: str, chat_id: str, request_context, message_id, parts) -> None:
    value = parts[2]
    if value in {"on", "off"}:
        set_meta(db, f"memory_mode:{chat_id}", value)
    send_memory_menu(token, chat_id, db, message_id, request_context=request_context)


def _handle_memoryscope(db: sqlite3.Connection, token: str, chat_id: str, request_context, message_id, parts) -> None:
    if parts[2] == "session":
        set_meta(db, f"memory_scope:{chat_id}", "session")
    send_memory_menu(token, chat_id, db, message_id, request_context=request_context)


def _handle_preset_save(
    db: sqlite3.Connection, token: str, chat_id: str, session: dict[str, str], message: dict, message_id
) -> None:
    pending_preset = {"session_id": session["session_id"], "expires_at": time.time() + PENDING_SETTINGS_TTL_SECONDS}
    set_meta(db, f"preset_save_input:{chat_id}", json.dumps(pending_preset))
    discard_panel_binding(db, chat_id, message_id)
    close_panel_message(db, token, chat_id, {"message": message})
    pending_preset["prompt_message_ids"] = send_text(
        token,
        chat_id,
        "Send a preset name (1–64 letters, numbers, hyphens, or underscores). Send /cancel to cancel.",
    )
    set_meta(db, f"preset_save_input:{chat_id}", json.dumps(pending_preset))


def _handle_preset_back(db: sqlite3.Connection, token: str, chat_id: str, request_context, message_id) -> None:
    send_preset_menu(token, chat_id, db, message_id, request_context=request_context)


def _handle_presetdelete(db: sqlite3.Connection, token: str, chat_id: str, request_context, message_id) -> None:
    send_preset_delete_menu(token, chat_id, db, message_id, request_context=request_context)


def _handle_presetpage(db: sqlite3.Connection, token: str, chat_id: str, request_context, message_id, parts) -> None:
    send_preset_menu(token, chat_id, db, message_id, int(parts[2]), request_context=request_context)


def _handle_presetdeletepage(
    db: sqlite3.Connection, token: str, chat_id: str, request_context, message_id, parts
) -> None:
    send_preset_delete_menu(token, chat_id, db, message_id, int(parts[2]), request_context=request_context)


def _handle_presetuse(
    db: sqlite3.Connection, token: str, chat_id: str, session: dict[str, str], request_context, message_id, parts
) -> None:
    apply_preset_action(
        db,
        token,
        chat_id,
        session["session_id"],
        "use",
        resolve_dynamic_callback_token(parts[2], "preset", chat_id, db=request_context.db) or "",
    )
    send_preset_menu(token, chat_id, db, message_id, request_context=request_context)


def _handle_presetdelconfirm(
    db: sqlite3.Connection, token: str, chat_id: str, session: dict[str, str], request_context, message_id, parts
) -> None:
    name = resolve_dynamic_callback_token(parts[2], "preset", chat_id, db=request_context.db) or ""
    apply_preset_action(db, token, chat_id, session["session_id"], "delete", name)
    send_preset_menu(token, chat_id, db, message_id, request_context=request_context)


def _handle_presetdel(db: sqlite3.Connection, token: str, chat_id: str, request_context, message_id, parts) -> None:
    name = resolve_dynamic_callback_token(parts[2], "preset", chat_id, db=request_context.db) or ""
    if name:
        send_preset_delete_confirm(token, chat_id, name, message_id, request_context=request_context)
    else:
        send_preset_menu(token, chat_id, db, message_id, request_context=request_context)


def _handle_rag_search(
    db: sqlite3.Connection,
    token: str,
    chat_id: str,
    session: dict[str, str],
    message: dict,
    input_flow_service: InputFlowService,
    request_context,
) -> None:
    input_flow_service.start_text_action(
        db,
        token,
        chat_id,
        session["session_id"],
        "databank_search",
        "Send a query to search the active Data Bank.",
        {"message": message},
        request_context=request_context,
    )


def _handle_rag_versions(db: sqlite3.Connection, token: str, chat_id: str, request_context, message_id) -> None:
    send_databank_versions_menu(token, chat_id, db, message_id, request_context=request_context)


def _handle_ragversionspage(
    db: sqlite3.Connection, token: str, chat_id: str, request_context, message_id, parts
) -> None:
    send_databank_versions_menu(token, chat_id, db, message_id, page=int(parts[2]), request_context=request_context)


def _handle_ragversions(db: sqlite3.Connection, token: str, chat_id: str, request_context, message_id, parts) -> None:
    filename = resolve_dynamic_callback_token(parts[2], "rag_document", chat_id, db=request_context.db) or ""
    send_databank_versions_menu(token, chat_id, db, message_id, filename=filename, request_context=request_context)


def _handle_ragactivate(
    db: sqlite3.Connection, token: str, chat_id: str, request_context, rag_service: RagService, message_id, parts
) -> None:
    raw = resolve_dynamic_callback_token(parts[2], "rag_version", chat_id, db=request_context.db) or ""
    filename, _, raw_version = raw.rpartition("|")
    try:
        version = int(raw_version)
    except ValueError:
        version = 0
    if filename and version > 0:
        rag_service.activate(db, chat_id, filename, version)
    send_databank_versions_menu(token, chat_id, db, message_id, filename=filename, request_context=request_context)


def _handle_rag_remove(db: sqlite3.Connection, token: str, chat_id: str, request_context, message_id) -> None:
    send_databank_remove_menu(token, chat_id, db, message_id, request_context=request_context)


def _handle_rag_back(db: sqlite3.Connection, token: str, chat_id: str, request_context, message_id) -> None:
    send_databank_menu(token, chat_id, db, message_id, request_context=request_context)


def _handle_ragremovepage(db: sqlite3.Connection, token: str, chat_id: str, request_context, message_id, parts) -> None:
    send_databank_remove_menu(token, chat_id, db, message_id, int(parts[2]), request_context=request_context)


def _handle_ragpage(db: sqlite3.Connection, token: str, chat_id: str, request_context, message_id, parts) -> None:
    send_databank_menu(token, chat_id, db, message_id, int(parts[2]), request_context=request_context)


def _handle_ragremoveconfirm(
    db: sqlite3.Connection, token: str, chat_id: str, request_context, rag_service: RagService, message_id, parts
) -> None:
    filename = resolve_dynamic_callback_token(parts[2], "rag_document", chat_id, db=request_context.db) or ""
    if filename:
        rag_service.remove(db, chat_id, filename)
    send_databank_menu(token, chat_id, db, message_id, request_context=request_context)


def _handle_ragremove(db: sqlite3.Connection, token: str, chat_id: str, request_context, message_id, parts) -> None:
    filename = resolve_dynamic_callback_token(parts[2], "rag_document", chat_id, db=request_context.db) or ""
    if filename:
        send_databank_remove_confirm(token, chat_id, filename, message_id, request_context=request_context)
    else:
        send_databank_menu(token, chat_id, db, message_id, request_context=request_context)


def _handle_rag_reindex(
    db: sqlite3.Connection, token: str, chat_id: str, request_context, rag_service: RagService, message_id
) -> None:
    rag_service.reindex(db, chat_id)
    send_databank_menu(token, chat_id, db, message_id, request_context=request_context)


def _handle_rag(
    db: sqlite3.Connection, token: str, chat_id: str, request_context, rag_service: RagService, message_id, parts
) -> None:
    value = parts[2]
    if value in {"on", "off"}:
        set_meta(db, f"rag_mode:{chat_id}", value)
    send_databank_menu(token, chat_id, db, message_id, request_context=request_context)


def _bind_routes(
    db: sqlite3.Connection,
    token: str,
    chat_id: str,
    session: dict[str, str],
    data: str,
    message: dict,
    *,
    input_flow_service: InputFlowService,
    request_context,
    rag_service: RagService,
):
    """Bind the existing action vocabulary to narrowly scoped operations."""
    message_id = message.get("message_id")
    parts = data.split(":", 2)
    return (
        {
            "enum:close": lambda: _handle_close(db, token, chat_id, message, message_id),
            "enum:stscript:cancel": lambda: _handle_close(db, token, chat_id, message, message_id),
            "enum:stscript:reset": lambda: _handle_stscript_reset(
                db, token, chat_id, message, request_context, message_id
            ),
            "enum:settings:reset": lambda: _handle_settings_reset(
                db, token, chat_id, session, request_context, message_id
            ),
            "enum:stt:language": lambda: _handle_stt_language(db, token, chat_id, request_context, message_id),
            "enum:stt:language_input": lambda: _handle_stt_language_input(
                db, token, chat_id, session, message, message_id
            ),
            "enum:stt:model": lambda: _handle_stt_model(db, token, chat_id, request_context, message_id),
            "enum:stt:back": lambda: _handle_stt_back(db, token, chat_id, request_context, message_id),
            "enum:memory:search": lambda: _handle_memory_search(
                db, token, chat_id, session, message, input_flow_service, request_context
            ),
            "enum:memory:scope": lambda: _handle_memory_scope(db, token, chat_id, request_context, message_id),
            "enum:memory:back": lambda: _handle_memory_scope(db, token, chat_id, request_context, message_id),
            "enum:preset:save": lambda: _handle_preset_save(db, token, chat_id, session, message, message_id),
            "enum:preset:back": lambda: _handle_preset_back(db, token, chat_id, request_context, message_id),
            "enum:presetdelete": lambda: _handle_presetdelete(db, token, chat_id, request_context, message_id),
            "enum:rag:search": lambda: _handle_rag_search(
                db, token, chat_id, session, message, input_flow_service, request_context
            ),
            "enum:rag:versions": lambda: _handle_rag_versions(db, token, chat_id, request_context, message_id),
            "enum:rag:remove": lambda: _handle_rag_remove(db, token, chat_id, request_context, message_id),
            "enum:rag:back": lambda: _handle_rag_back(db, token, chat_id, request_context, message_id),
            "enum:rag:reindex": lambda: _handle_rag_reindex(
                db, token, chat_id, request_context, rag_service, message_id
            ),
        },
        (
            (
                "enum:settings:input:",
                lambda: _handle_settings_input(db, token, chat_id, session, data, message, message_id),
            ),
            (
                "enum:settings:reasoning:",
                lambda: _handle_settings_reasoning(db, token, chat_id, session, data, request_context, message_id),
            ),
            (
                "enum:humanizer:",
                lambda: _handle_humanizer(db, token, chat_id, session, data, request_context, message_id),
            ),
            (
                "enum:grounded:",
                lambda: _handle_grounded(db, token, chat_id, session, data, request_context, message_id),
            ),
            ("enum:stream:", lambda: _handle_stream(db, token, chat_id, request_context, message_id, parts)),
            ("enum:voice:", lambda: _handle_voice(db, token, chat_id, request_context, message_id, parts)),
            (
                "enum:sttlanguagepage:",
                lambda: _handle_sttlanguagepage(db, token, chat_id, request_context, message_id, parts),
            ),
            ("enum:sttlanguage:", lambda: _handle_sttlanguage(db, token, chat_id, request_context, message_id, parts)),
            ("enum:stt:", lambda: _handle_stt(db, token, chat_id, request_context, message_id, parts)),
            ("enum:sttmodel:", lambda: _handle_sttmodel(db, token, chat_id, request_context, message_id, parts)),
            ("enum:memory:", lambda: _handle_memory(db, token, chat_id, request_context, message_id, parts)),
            ("enum:memoryscope:", lambda: _handle_memoryscope(db, token, chat_id, request_context, message_id, parts)),
            ("enum:presetpage:", lambda: _handle_presetpage(db, token, chat_id, request_context, message_id, parts)),
            (
                "enum:presetdeletepage:",
                lambda: _handle_presetdeletepage(db, token, chat_id, request_context, message_id, parts),
            ),
            (
                "enum:presetuse:",
                lambda: _handle_presetuse(db, token, chat_id, session, request_context, message_id, parts),
            ),
            (
                "enum:presetdelconfirm:",
                lambda: _handle_presetdelconfirm(db, token, chat_id, session, request_context, message_id, parts),
            ),
            ("enum:presetdel:", lambda: _handle_presetdel(db, token, chat_id, request_context, message_id, parts)),
            (
                "enum:ragversionspage:",
                lambda: _handle_ragversionspage(db, token, chat_id, request_context, message_id, parts),
            ),
            ("enum:ragversions:", lambda: _handle_ragversions(db, token, chat_id, request_context, message_id, parts)),
            (
                "enum:ragactivate:",
                lambda: _handle_ragactivate(db, token, chat_id, request_context, rag_service, message_id, parts),
            ),
            (
                "enum:ragremovepage:",
                lambda: _handle_ragremovepage(db, token, chat_id, request_context, message_id, parts),
            ),
            ("enum:ragpage:", lambda: _handle_ragpage(db, token, chat_id, request_context, message_id, parts)),
            (
                "enum:ragremoveconfirm:",
                lambda: _handle_ragremoveconfirm(db, token, chat_id, request_context, rag_service, message_id, parts),
            ),
            ("enum:ragremove:", lambda: _handle_ragremove(db, token, chat_id, request_context, message_id, parts)),
            ("enum:rag:", lambda: _handle_rag(db, token, chat_id, request_context, rag_service, message_id, parts)),
        ),
    )


def handle_enum_callback(
    db: sqlite3.Connection,
    token: str,
    chat_id: str,
    session: dict[str, str],
    data: str,
    message: dict,
    *,
    input_flow_service: InputFlowService,
    request_context,
    rag_service: RagService,
) -> None:
    """Select one explicit exact/prefix route without changing callback action policy."""
    exact, prefixes = _bind_routes(
        db,
        token,
        chat_id,
        session,
        data,
        message,
        input_flow_service=input_flow_service,
        request_context=request_context,
        rag_service=rag_service,
    )
    handler = exact.get(data)
    if handler is None:
        handler = next((call for prefix, call in prefixes if data.startswith(prefix)), None)
    if handler is not None:
        handler()
