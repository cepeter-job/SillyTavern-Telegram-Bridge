"""Canonical feature callbacks owner."""

from __future__ import annotations

import logging

from bridge.callback_tokens import resolve_dynamic_callback_token
from bridge.callbacks import close_panel_message
from bridge.card_content import card_fields_from_file
from bridge.cards import send_panel_message
from bridge.director_goals import set_director_goal
from bridge.feature_panels import send_curated_memory_menu, send_director_goal_menu, send_scene_menu
from bridge.group_commands import handle_summary_command
from bridge.group_service import GroupService
from bridge.image_generation import (
    IMAGE_PROMPT_MAX_CHARS,
    handle_imagine_scene,
    image_prompt_max_chars,
    reset_session_image_settings,
    session_image_settings,
    set_session_image_model,
    set_session_image_size,
)
from bridge.image_panels import (
    send_imagine_menu,
    send_imagine_model_menu,
    send_imagine_options_menu,
    send_imagine_size_menu,
)
from bridge.memory_backend import memory_mode
from bridge.memory_curator import curate_memory_now
from bridge.memory_panels import send_memory_menu
from bridge.prompt_panels import send_prompt_menu
from bridge.provider_port import ProviderPort
from bridge.scene_state import clear_scene_state, refresh_scene_state_now
from bridge.status_panels import status_text
from bridge.telegram import send_text, send_typing
from bridge.text_action_input import start_text_action_input


def handle_prompt_and_feature_callback(
    db,
    token,
    callback,
    answer_callback,
    data,
    chat_id,
    message,
    session,
    session_id,
    operation_id,
    *,
    group_service: GroupService,
    provider_port: ProviderPort,
    request_context,
    memory_service,
):
    """Handle prompt and feature callbacks; status itself is text-only."""
    message_id = message.get("message_id")
    if data == "prompt:close":
        answer_callback(token, str(callback.get("id", "")), "Closed")
        close_panel_message(db, token, chat_id, callback)
        return True
    if data == "prompt:menu":
        send_prompt_menu(
            token,
            chat_id,
            db,
            session,
            card_fields_from_file(session["character_file"], app_settings=request_context.app_settings),
            message_id,
            group_service=group_service,
            memory_service=memory_service,
            request_context=request_context,
        )
        return True
    if data.startswith("prompt:"):
        if data == "prompt:status":
            send_text(
                token,
                chat_id,
                status_text(
                    db,
                    chat_id,
                    session,
                    card_fields_from_file(session["character_file"], app_settings=request_context.app_settings),
                    session.get("model_id") or request_context.app_settings.default_model,
                    session.get("persona_id") or "",
                    group_service=group_service,
                    app_settings=request_context.app_settings,
                ),
            )
        elif data.rsplit(":", 1)[1] in {"budget", "memory", "group"}:
            send_prompt_menu(
                token,
                chat_id,
                db,
                session,
                card_fields_from_file(session["character_file"], app_settings=request_context.app_settings),
                message_id,
                data.rsplit(":", 1)[1],
                group_service=group_service,
                memory_service=memory_service,
                request_context=request_context,
            )
        return True
    if data.startswith(("scene:", "imagine:", "goal:", "curated:", "summary:")):
        return handle_feature_panel_callback(
            db,
            token,
            callback,
            answer_callback,
            data,
            chat_id,
            message,
            session,
            session_id,
            operation_id,
            group_service=group_service,
            provider_port=provider_port,
            request_context=request_context,
        )
    return False


def handle_feature_panel_callback(
    db,
    token,
    callback,
    answer_callback,
    data,
    chat_id,
    message,
    session,
    session_id,
    operation_id,
    *,
    group_service: GroupService,
    provider_port: ProviderPort,
    request_context,
):
    message_id = message.get("message_id")
    if data.startswith("summary:"):
        action = data.split(":", 1)[1]
        if action == "cancel" or action == "close":
            answer_callback(token, str(callback.get("id", "")), "Cancelled")
            close_panel_message(db, token, chat_id, callback)
        elif action == "confirm":
            answer_callback(token, str(callback.get("id", "")), "Summarizing")
            handle_summary_command(
                db, token, chat_id, session, provider_port=provider_port, app_settings=request_context.app_settings
            )
        return True
    if data.startswith("imagine:"):
        action = data.split(":", 1)[1]
        if action == "close":
            answer_callback(token, str(callback.get("id", "")), "Closed")
            close_panel_message(db, token, chat_id, callback)
        elif action == "custom":
            answer_callback(token, str(callback.get("id", "")), "Send prompt")
            prompt_max_chars = IMAGE_PROMPT_MAX_CHARS
            try:
                selection, _size = session_image_settings(
                    db,
                    chat_id,
                    session_id,
                    app_settings=request_context.app_settings,
                )
                prompt_max_chars = image_prompt_max_chars(selection, app_settings=request_context.app_settings)
            except ValueError:
                pass
            start_text_action_input(
                db,
                token,
                chat_id,
                session_id,
                "imagine",
                f"Send a custom image prompt (1–{prompt_max_chars:,} characters).",
                callback,
            )
        elif action == "scene":
            answer_callback(token, str(callback.get("id", "")), "Generating current scene")
            send_typing(token, chat_id)
            try:
                handle_imagine_scene(
                    db,
                    token,
                    chat_id,
                    session,
                    card_fields_from_file(session["character_file"], app_settings=request_context.app_settings),
                    provider_port=provider_port,
                    app_settings=request_context.app_settings,
                )
            except ValueError as exc:
                send_text(token, chat_id, f"Image generation unavailable: {exc}")
            except Exception:
                logging.warning("Current-scene image generation failed", exc_info=True)
                send_text(token, chat_id, "Current-scene image generation failed. Open /imagine and retry.")
        elif action == "menu":
            answer_callback(token, str(callback.get("id", "")), "Image menu")
            send_imagine_menu(
                token,
                chat_id,
                db,
                session,
                message_id,
                request_context=request_context,
            )
        elif action == "options":
            answer_callback(token, str(callback.get("id", "")), "Image options")
            send_imagine_options_menu(
                token,
                chat_id,
                db,
                session,
                message_id,
                request_context=request_context,
            )
        elif action == "model":
            answer_callback(token, str(callback.get("id", "")), "Choose image model")
            send_imagine_model_menu(
                token,
                chat_id,
                db,
                session,
                message_id,
                request_context=request_context,
            )
        elif action.startswith("model:"):
            handle = action.split(":", 1)[1]
            selection = resolve_dynamic_callback_token(handle, "image_model", chat_id, db=db)
            if selection is None:
                answer_callback(token, str(callback.get("id", "")), "Image model choice expired")
                send_imagine_model_menu(
                    token,
                    chat_id,
                    db,
                    session,
                    message_id,
                    request_context=request_context,
                )
            else:
                try:
                    set_session_image_model(
                        db,
                        chat_id,
                        session_id,
                        selection,
                        app_settings=request_context.app_settings,
                    )
                except ValueError:
                    answer_callback(token, str(callback.get("id", "")), "Image model is no longer available")
                    send_imagine_model_menu(
                        token,
                        chat_id,
                        db,
                        session,
                        message_id,
                        request_context=request_context,
                    )
                else:
                    answer_callback(token, str(callback.get("id", "")), "Image model updated")
                    send_imagine_options_menu(
                        token,
                        chat_id,
                        db,
                        session,
                        message_id,
                        request_context=request_context,
                    )
        elif action == "size":
            answer_callback(token, str(callback.get("id", "")), "Choose image size")
            send_imagine_size_menu(
                token,
                chat_id,
                db,
                session,
                message_id,
                request_context=request_context,
            )
        elif action.startswith("size:"):
            size = action.split(":", 1)[1]
            try:
                set_session_image_size(db, chat_id, session_id, size)
            except ValueError:
                answer_callback(token, str(callback.get("id", "")), "Unsupported image size")
                send_imagine_size_menu(
                    token,
                    chat_id,
                    db,
                    session,
                    message_id,
                    request_context=request_context,
                )
            else:
                answer_callback(token, str(callback.get("id", "")), "Image size updated")
                send_imagine_options_menu(
                    token,
                    chat_id,
                    db,
                    session,
                    message_id,
                    request_context=request_context,
                )
        elif action == "reset":
            reset_session_image_settings(db, chat_id, session_id)
            answer_callback(token, str(callback.get("id", "")), "Image defaults restored")
            send_imagine_options_menu(
                token,
                chat_id,
                db,
                session,
                message_id,
                request_context=request_context,
            )
        return True
    if data.startswith("scene:"):
        action = data.split(":", 1)[1]
        if action == "close":
            answer_callback(token, str(callback.get("id", "")), "Closed")
            close_panel_message(db, token, chat_id, callback)
        elif action == "status":
            send_text(
                token,
                chat_id,
                status_text(
                    db,
                    chat_id,
                    session,
                    card_fields_from_file(session["character_file"], app_settings=request_context.app_settings),
                    session.get("model_id") or request_context.app_settings.default_model,
                    session.get("persona_id") or "",
                    group_service=group_service,
                    app_settings=request_context.app_settings,
                ),
            )
        elif action == "refresh":
            send_typing(token, chat_id)
            refresh_scene_state_now(
                db,
                "",
                chat_id,
                session,
                str(
                    card_fields_from_file(session["character_file"], app_settings=request_context.app_settings).get(
                        "name"
                    )
                    or "unknown"
                ),
                provider_port=provider_port,
                app_settings=request_context.app_settings,
            )
            send_scene_menu(token, chat_id, db, session, message_id, request_context=request_context)
        elif action == "clear":
            send_panel_message(
                token,
                chat_id,
                "Clear the stored structured scene state?",
                {
                    "inline_keyboard": [
                        [{"text": "✅ Confirm clear", "callback_data": "scene:clear_confirm"}],
                        [
                            {"text": "⬅️ Back", "callback_data": "scene:status"},
                            {"text": "❌ Close", "callback_data": "scene:close"},
                        ],
                    ]
                },
                message_id,
                request_context=request_context,
            )
        elif action == "clear_confirm":
            clear_scene_state(db, chat_id, session_id)
            answer_callback(token, str(callback.get("id", "")), "Scene cleared")
            send_scene_menu(token, chat_id, db, session, message_id, request_context=request_context)
        return True
    if data.startswith("goal:"):
        action = data.split(":", 1)[1]
        if action == "close":
            answer_callback(token, str(callback.get("id", "")), "Closed")
            close_panel_message(db, token, chat_id, callback)
        elif action == "set":
            start_text_action_input(
                db,
                token,
                chat_id,
                session_id,
                "director_goal",
                "Send the hidden Director objective (up to 1,200 characters).",
                callback,
            )
        elif action == "clear":
            set_director_goal(db, chat_id, session_id, "")
            answer_callback(token, str(callback.get("id", "")), "Objective cleared")
            send_director_goal_menu(token, chat_id, db, session, message_id, request_context=request_context)
        return True
    if data.startswith("curated:"):
        action = data.split(":", 1)[1]
        if action == "close":
            answer_callback(token, str(callback.get("id", "")), "Closed")
            close_panel_message(db, token, chat_id, callback)
        elif action == "refresh":
            if memory_mode(db, chat_id) != "on":
                send_text(token, chat_id, "Hindsight memory is off. Enable /memory first.")
            else:
                send_typing(token, chat_id)
                curate_memory_now(
                    db,
                    "",
                    chat_id,
                    session,
                    str(
                        card_fields_from_file(session["character_file"], app_settings=request_context.app_settings).get(
                            "name"
                        )
                        or "unknown"
                    ),
                    provider_port=provider_port,
                    app_settings=request_context.app_settings,
                )
                send_curated_memory_menu(token, chat_id, db, session, message_id, request_context=request_context)
        elif action == "back":
            send_memory_menu(token, chat_id, db, message_id, request_context=request_context)
        return True
    return False
