"""Panel-first image-generation presentation."""

from __future__ import annotations

from bridge.callback_tokens import dynamic_callback_token
from bridge.cards import send_panel_message
from bridge.image_reference import load_character_reference
from bridge.image_routing import (
    AUTO_IMAGE_SELECTION,
    IMAGE_SIZE_PRESETS,
    image_model_options,
    resolve_image_route,
    session_image_settings,
)
from bridge.image_styles import IMAGE_STYLE_OPTIONS, session_image_style


def _short_model(selection: str) -> str:
    return selection.split("::", 1)[-1] if selection else "Not configured"


def _display_settings(
    selection: str,
    size: str,
    character_file: str,
    *,
    app_settings,
) -> tuple[str, str]:
    model_label = _short_model(selection)
    size_label = size
    try:
        reference = load_character_reference(character_file, app_settings=app_settings)
        route = resolve_image_route(
            selection,
            reference_available=reference is not None,
            app_settings=app_settings,
        )
    except ValueError:
        return model_label, size_label

    if selection == AUTO_IMAGE_SELECTION:
        model_label = f"Auto → {route.model}"
        if route.transport == "reference":
            model_label += " · character reference"
    if route.transport == "reference" and route.model.casefold() == "step-image-edit-2" and size != "1024x1024":
        size_label = "Auto (reference model)"
    return model_label, size_label


def imagine_panel(
    db,
    chat_id: str,
    session_id: str,
    *,
    character_file: str = "",
    app_settings,
) -> tuple[str, dict]:
    try:
        selection, size = session_image_settings(
            db,
            chat_id,
            session_id,
            app_settings=app_settings,
        )
        model_label, size_label = _display_settings(
            selection,
            size,
            character_file,
            app_settings=app_settings,
        )
        settings_line = f"Model: {model_label}\nSize: {size_label}"
    except ValueError:
        settings_line = "Model: Not configured\nSize: 1024x1024"

    style = session_image_style(db, chat_id, session_id)
    text = (
        "Image generation\n\n"
        "Choose a style and how to build the image.\n\n"
        "🎬 Current Scene — visualize the latest committed roleplay scene using "
        "structured scene state and recent story context. This does not alter the story.\n"
        "✏️ Custom Prompt — enter a one-off image prompt after choosing it here.\n\n"
        f"Style: {style.title()}\n"
        f"{settings_line}"
    )
    markup = {
        "inline_keyboard": [
            [
                {
                    "text": ("✅ " if value == style else "") + label,
                    "callback_data": f"imagine:style:{value}",
                }
                for value, label in IMAGE_STYLE_OPTIONS
            ],
            [{"text": "🎬 Current Scene", "callback_data": "imagine:scene"}],
            [{"text": "✏️ Custom Prompt", "callback_data": "imagine:custom"}],
            [{"text": "⚙️ Options", "callback_data": "imagine:options"}],
            [{"text": "❌ Close", "callback_data": "imagine:close"}],
        ]
    }
    return text, markup


def imagine_options_panel(
    db,
    chat_id: str,
    session_id: str,
    *,
    character_file: str = "",
    app_settings,
) -> tuple[str, dict]:
    try:
        selection, size = session_image_settings(
            db,
            chat_id,
            session_id,
            app_settings=app_settings,
        )
        model, size = _display_settings(
            selection,
            size,
            character_file,
            app_settings=app_settings,
        )
    except ValueError:
        model, size = "Not configured", "1024x1024"

    style = session_image_style(db, chat_id, session_id)
    text = (
        "Image options\n\n"
        f"Style: {style.title()}\n"
        f"Model: {model}\n"
        f"Size: {size}\n\n"
        "These settings are scoped to the active session and apply to both "
        "Current Scene and Custom Prompt."
    )
    markup = {
        "inline_keyboard": [
            [
                {"text": "🖼 Model", "callback_data": "imagine:model"},
                {"text": "📐 Size", "callback_data": "imagine:size"},
            ],
            [{"text": "↩️ Reset defaults", "callback_data": "imagine:reset"}],
            [
                {"text": "⬅️ Back", "callback_data": "imagine:menu"},
                {"text": "❌ Close", "callback_data": "imagine:close"},
            ],
        ]
    }
    return text, markup


def imagine_model_panel(
    db,
    chat_id: str,
    session_id: str,
    *,
    app_settings,
) -> tuple[str, dict]:
    options = image_model_options(app_settings=app_settings)
    try:
        selected, _size = session_image_settings(
            db,
            chat_id,
            session_id,
            app_settings=app_settings,
        )
    except ValueError:
        selected = ""

    rows = []
    for selection, label in options[:20]:
        token = dynamic_callback_token("image_model", selection, chat_id, db=db)
        prefix = "✅ " if selection == selected else ""
        rows.append(
            [
                {
                    "text": (prefix + label)[:64],
                    "callback_data": f"imagine:model:{token}",
                }
            ]
        )
    if not rows:
        rows.append([{"text": "No image models configured", "callback_data": "imagine:options"}])
    rows.append(
        [
            {"text": "⬅️ Back", "callback_data": "imagine:options"},
            {"text": "❌ Close", "callback_data": "imagine:close"},
        ]
    )
    return "Image model\n\nChoose the model for this session.", {"inline_keyboard": rows}


def imagine_size_panel(
    db,
    chat_id: str,
    session_id: str,
    *,
    app_settings,
) -> tuple[str, dict]:
    try:
        _selection, selected = session_image_settings(
            db,
            chat_id,
            session_id,
            app_settings=app_settings,
        )
    except ValueError:
        selected = "1024x1024"

    rows = []
    for label, size in IMAGE_SIZE_PRESETS:
        prefix = "✅ " if size == selected else ""
        rows.append(
            [
                {
                    "text": f"{prefix}{label} · {size}",
                    "callback_data": f"imagine:size:{size}",
                }
            ]
        )
    rows.append(
        [
            {"text": "⬅️ Back", "callback_data": "imagine:options"},
            {"text": "❌ Close", "callback_data": "imagine:close"},
        ]
    )
    return "Image size\n\nChoose the output aspect preset for this session.", {"inline_keyboard": rows}


def _send(token, chat_id, text, markup, message_id, *, request_context) -> None:
    send_panel_message(
        token,
        chat_id,
        text,
        markup,
        message_id,
        request_context=request_context,
    )


def send_imagine_menu(token, chat_id, db, session, message_id=None, *, request_context) -> None:
    text, markup = imagine_panel(
        db,
        chat_id,
        str(session["session_id"]),
        character_file=str(session.get("character_file") or ""),
        app_settings=request_context.app_settings,
    )
    _send(token, chat_id, text, markup, message_id, request_context=request_context)


def send_imagine_options_menu(token, chat_id, db, session, message_id=None, *, request_context) -> None:
    text, markup = imagine_options_panel(
        db,
        chat_id,
        str(session["session_id"]),
        character_file=str(session.get("character_file") or ""),
        app_settings=request_context.app_settings,
    )
    _send(token, chat_id, text, markup, message_id, request_context=request_context)


def send_imagine_model_menu(token, chat_id, db, session, message_id=None, *, request_context) -> None:
    text, markup = imagine_model_panel(
        db,
        chat_id,
        str(session["session_id"]),
        app_settings=request_context.app_settings,
    )
    _send(token, chat_id, text, markup, message_id, request_context=request_context)


def send_imagine_size_menu(token, chat_id, db, session, message_id=None, *, request_context) -> None:
    text, markup = imagine_size_panel(
        db,
        chat_id,
        str(session["session_id"]),
        app_settings=request_context.app_settings,
    )
    _send(token, chat_id, text, markup, message_id, request_context=request_context)
