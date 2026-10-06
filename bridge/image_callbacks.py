"""Image panel callbacks and scoped prompt input flows."""

from __future__ import annotations

import logging

from bridge.callback_tokens import resolve_dynamic_callback_token
from bridge.callbacks import close_panel_message
from bridge.image_generation import (
    IMAGE_PROMPT_MAX_CHARS,
    handle_imagine_scene,
    image_prompt_max_chars,
    image_provider_error_message,
    imagine_prompt_input_max_chars,
)
from bridge.image_panels import (
    send_imagine_menu,
    send_imagine_model_menu,
    send_imagine_options_menu,
    send_imagine_size_menu,
)
from bridge.image_routing import (
    reset_session_image_settings,
    session_image_settings,
    set_session_image_model,
    set_session_image_size,
)
from bridge.image_styles import image_style_prompt_prefix, session_image_style, set_session_image_style
from bridge.provider_errors import ProviderRequestError
from bridge.telegram import send_text, send_typing
from bridge.text_action_input import start_text_action_input


def _imagine_close(db, token, callback, answer_callback, chat_id):
    answer_callback(token, str(callback.get("id", "")), "Closed")
    close_panel_message(db, token, chat_id, callback)
    return True


def _imagine_custom(db, token, callback, answer_callback, chat_id, session, request_context):
    answer_callback(token, str(callback.get("id", "")), "Send prompt")
    session_id = str(session["session_id"])
    style = session_image_style(db, chat_id, session_id)
    style_overhead = len(image_style_prompt_prefix(style))
    prompt_max_chars = IMAGE_PROMPT_MAX_CHARS - style_overhead
    try:
        selection, _size = session_image_settings(
            db,
            chat_id,
            session_id,
            app_settings=request_context.app_settings,
        )
        prompt_max_chars = imagine_prompt_input_max_chars(
            selection,
            str(session.get("character_file") or ""),
            app_settings=request_context.app_settings,
            style=style,
        )
    except ValueError:
        try:
            prompt_max_chars = (
                image_prompt_max_chars(selection, app_settings=request_context.app_settings) - style_overhead
            )
        except (ValueError, UnboundLocalError):
            pass
    start_text_action_input(
        db,
        token,
        chat_id,
        session_id,
        "imagine",
        f"Send a custom image prompt (1–{prompt_max_chars:,} characters). Style: {style.title()}.",
        callback,
    )
    return True


def _imagine_scene(db, token, callback, answer_callback, chat_id, session, provider_port, request_context):
    answer_callback(token, str(callback.get("id", "")), "Generating current scene")
    send_typing(token, chat_id)
    try:
        handle_imagine_scene(
            db,
            token,
            chat_id,
            session,
            provider_port=provider_port,
            app_settings=request_context.app_settings,
        )
    except ProviderRequestError as exc:
        logging.warning("Current-scene image provider request failed: %s", exc)
        send_text(token, chat_id, image_provider_error_message(exc))
    except ValueError as exc:
        send_text(token, chat_id, f"Image generation unavailable: {exc}")
    except Exception:
        logging.warning("Current-scene image generation failed", exc_info=True)
        send_text(token, chat_id, "Current-scene image generation failed. Open /imagine and retry.")
    return True


def _imagine_menu(db, token, callback, answer_callback, chat_id, session, request_context, message_id):
    answer_callback(token, str(callback.get("id", "")), "Image menu")
    send_imagine_menu(
        token,
        chat_id,
        db,
        session,
        message_id,
        request_context=request_context,
    )
    return True


def _imagine_options(db, token, callback, answer_callback, chat_id, session, request_context, message_id):
    answer_callback(token, str(callback.get("id", "")), "Image options")
    send_imagine_options_menu(
        token,
        chat_id,
        db,
        session,
        message_id,
        request_context=request_context,
    )
    return True


def _imagine_model(db, token, callback, answer_callback, chat_id, session, request_context, message_id):
    answer_callback(token, str(callback.get("id", "")), "Choose image model")
    send_imagine_model_menu(
        token,
        chat_id,
        db,
        session,
        message_id,
        request_context=request_context,
    )
    return True


def _imagine_model_selection(
    db, token, callback, answer_callback, chat_id, session, session_id, request_context, message_id, action
):
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
    return True


def _imagine_size(db, token, callback, answer_callback, chat_id, session, request_context, message_id):
    answer_callback(token, str(callback.get("id", "")), "Choose image size")
    send_imagine_size_menu(
        token,
        chat_id,
        db,
        session,
        message_id,
        request_context=request_context,
    )
    return True


def _imagine_size_selection(
    db, token, callback, answer_callback, chat_id, session, session_id, request_context, message_id, action
):
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
    return True


def _imagine_style_selection(
    db, token, callback, answer_callback, chat_id, session, session_id, request_context, message_id, action
):
    style = action.split(":", 1)[1]
    try:
        set_session_image_style(db, chat_id, session_id, style)
    except ValueError:
        answer_callback(token, str(callback.get("id", "")), "Unsupported image style")
    else:
        answer_callback(token, str(callback.get("id", "")), "Image style updated")
    send_imagine_options_menu(token, chat_id, db, session, message_id, request_context=request_context)
    return True


def _imagine_reset(db, token, callback, answer_callback, chat_id, session, session_id, request_context, message_id):
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


def handle_imagine_callback(
    db, token, callback, answer_callback, data, chat_id, message, session, session_id, provider_port, request_context
):
    message_id = message.get("message_id")
    action = data.split(":", 1)[1]
    exact = {
        "close": lambda: _imagine_close(db, token, callback, answer_callback, chat_id),
        "custom": lambda: _imagine_custom(db, token, callback, answer_callback, chat_id, session, request_context),
        "scene": lambda: _imagine_scene(
            db, token, callback, answer_callback, chat_id, session, provider_port, request_context
        ),
        "menu": lambda: _imagine_menu(
            db, token, callback, answer_callback, chat_id, session, request_context, message_id
        ),
        "options": lambda: _imagine_options(
            db, token, callback, answer_callback, chat_id, session, request_context, message_id
        ),
        "model": lambda: _imagine_model(
            db, token, callback, answer_callback, chat_id, session, request_context, message_id
        ),
        "size": lambda: _imagine_size(
            db, token, callback, answer_callback, chat_id, session, request_context, message_id
        ),
        "reset": lambda: _imagine_reset(
            db, token, callback, answer_callback, chat_id, session, session_id, request_context, message_id
        ),
    }
    prefixes = (
        (
            "style:",
            lambda: _imagine_style_selection(
                db, token, callback, answer_callback, chat_id, session, session_id, request_context, message_id, action
            ),
        ),
        (
            "model:",
            lambda: _imagine_model_selection(
                db, token, callback, answer_callback, chat_id, session, session_id, request_context, message_id, action
            ),
        ),
        (
            "size:",
            lambda: _imagine_size_selection(
                db, token, callback, answer_callback, chat_id, session, session_id, request_context, message_id, action
            ),
        ),
    )
    handler = exact.get(action)
    if handler is None:
        handler = next((call for prefix, call in prefixes if action.startswith(prefix)), None)
    return handler() if handler is not None else True
