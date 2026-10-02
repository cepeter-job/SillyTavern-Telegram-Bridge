"""Canonical provider callbacks owner."""

from __future__ import annotations

import json
import time
from functools import partial

from bridge.callback_tokens import resolve_dynamic_callback_token
from bridge.callbacks import remove_inline_keyboard
from bridge.config import REASONING_LEVELS
from bridge.generation_settings import update_generation_settings
from bridge.limits import PENDING_SETTINGS_TTL_SECONDS
from bridge.metadata import set_meta
from bridge.model_selection import (
    clear_model_target_selection,
    get_model_target_selection,
    set_model_target_selection,
    set_task_model,
    set_utility_reasoning,
    task_model_for_session,
)
from bridge.port_contracts import ProviderPolicy, ProviderProbes
from bridge.provider_discovery import refresh_model_catalog
from bridge.provider_panel_tokens import load_provider_report, resolve_provider_action
from bridge.provider_panels import (
    send_model_menu,
    send_model_target_menu,
    send_provider_health_menu,
    send_story_reasoning_menu,
    send_utility_reasoning_menu,
)
from bridge.session_core import update_session
from bridge.telegram import send_text


def _catalog_current_model(db, chat_id: str, session: dict, session_id: str, *, request_context) -> str:
    if db is not None and get_model_target_selection(db, chat_id, session_id) == "utility":
        return task_model_for_session(db, chat_id, session, "utility", app_settings=request_context.app_settings)
    return session["model_id"] or request_context.app_settings.default_model


def _handle_models_providers(
    db, token, callback, answer_callback, data, chat_id, session, session_id, request_context, message_id, send_models
):
    page = int(data.rsplit(":", 1)[1])
    answer_callback(token, str(callback.get("id", "")), "Page")
    send_models(
        token,
        chat_id,
        _catalog_current_model(db, chat_id, session, session_id, request_context=request_context),
        message_id=message_id,
        page=page,
        request_context=request_context,
    )
    return True


def _handle_models_model(
    db, token, callback, answer_callback, data, chat_id, session, session_id, request_context, message_id, send_models
):
    parts = data.split(":")
    provider_id = resolve_dynamic_callback_token(parts[2], "provider", chat_id, db=db) or ""
    page = int(parts[3])
    answer_callback(token, str(callback.get("id", "")), "Page")
    send_models(
        token,
        chat_id,
        _catalog_current_model(db, chat_id, session, session_id, request_context=request_context),
        provider_id,
        message_id,
        page,
        request_context=request_context,
    )
    return True


def _handle_models_target(
    db, token, callback, answer_callback, chat_id, session, session_id, request_context, message_id
):
    answer_callback(token, str(callback.get("id", "")), "Back to target")
    send_model_target_menu(
        token,
        chat_id,
        _catalog_current_model(db, chat_id, session, session_id, request_context=request_context),
        task_model_for_session(db, chat_id, session, "utility", app_settings=request_context.app_settings),
        message_id,
        request_context=request_context,
    )
    return True


def _handle_models_cancel(db, token, callback, answer_callback):
    answer_callback(token, str(callback.get("id", "")), "Cancelled")
    remove_inline_keyboard(db, token, callback)
    return True


def _handle_models_story_reasoning(token, callback, answer_callback, chat_id, request_context, message_id):
    answer_callback(token, str(callback.get("id", "")), "Story reasoning")
    send_story_reasoning_menu(
        token,
        chat_id,
        message_id,
        request_context=request_context,
    )
    return True


def _handle_storyreasoning(
    db, token, callback, answer_callback, data, chat_id, session_id, request_context, message_id
):
    label = data.split(":", 1)[1]
    if label == "custom":
        pending = {
            "key": "reasoning_budget",
            "scope": "story_reasoning",
            "session_id": session_id,
            "expires_at": time.time() + PENDING_SETTINGS_TTL_SECONDS,
        }
        set_meta(db, f"settings_input:{chat_id}", json.dumps(pending))
        answer_callback(token, str(callback.get("id", "")), "Enter Story reasoning")
        remove_inline_keyboard(db, token, callback)
        pending["prompt_message_ids"] = send_text(
            token,
            chat_id,
            "Send Story reasoning budget (0–32000). Send /cancel to leave it unchanged.",
        )
        set_meta(db, f"settings_input:{chat_id}", json.dumps(pending))
        return True
    if label not in REASONING_LEVELS:
        answer_callback(token, str(callback.get("id", "")), "Reasoning choice invalid")
        return True
    update_generation_settings(db, chat_id, session_id, reasoning_budget=REASONING_LEVELS[label])
    answer_callback(token, str(callback.get("id", "")), "Story reasoning updated")
    send_story_reasoning_menu(
        token,
        chat_id,
        message_id,
        request_context=request_context,
    )
    return True


def _handle_models_utility_reasoning(token, callback, answer_callback, chat_id, request_context, message_id):
    answer_callback(token, str(callback.get("id", "")), "Utility reasoning")
    send_utility_reasoning_menu(
        token,
        chat_id,
        message_id,
        request_context=request_context,
    )
    return True


def _handle_utilityreasoning(
    db, token, callback, answer_callback, data, chat_id, session_id, request_context, message_id
):
    label = data.split(":", 1)[1]
    if label == "custom":
        pending = {
            "key": "reasoning_budget",
            "scope": "utility_reasoning",
            "session_id": session_id,
            "expires_at": time.time() + PENDING_SETTINGS_TTL_SECONDS,
        }
        set_meta(db, f"settings_input:{chat_id}", json.dumps(pending))
        answer_callback(token, str(callback.get("id", "")), "Enter Utility reasoning")
        remove_inline_keyboard(db, token, callback)
        pending["prompt_message_ids"] = send_text(
            token,
            chat_id,
            "Send Utility reasoning budget (0–32000). Send /cancel to leave it unchanged.",
        )
        set_meta(db, f"settings_input:{chat_id}", json.dumps(pending))
        return True
    if label not in REASONING_LEVELS:
        answer_callback(token, str(callback.get("id", "")), "Reasoning choice invalid")
        return True
    set_utility_reasoning(db, chat_id, session_id, REASONING_LEVELS[label])
    answer_callback(token, str(callback.get("id", "")), "Utility reasoning updated")
    send_utility_reasoning_menu(
        token,
        chat_id,
        message_id,
        request_context=request_context,
    )
    return True


def _handle_models_back(
    db, token, callback, answer_callback, chat_id, session, session_id, request_context, message_id, send_models
):
    answer_callback(token, str(callback.get("id", "")), "Back to providers")
    send_models(
        token,
        chat_id,
        _catalog_current_model(db, chat_id, session, session_id, request_context=request_context),
        message_id=message_id,
        request_context=request_context,
    )
    return True


def _handle_provider_health(
    token,
    callback,
    answer_callback,
    chat_id,
    request_context,
    provider_policy: ProviderPolicy | None,
    message_id,
    send_health,
):
    answer_callback(token, str(callback.get("id", "")), "Health")
    send_health(token, chat_id, message_id, request_context=request_context, provider_policy=provider_policy)
    return True


def _handle_provider_refresh(
    db, token, callback, answer_callback, chat_id, session, session_id, request_context, message_id, send_models
):
    answer_callback(token, str(callback.get("id", "")), "Refreshing")
    _config, refreshed, failed = refresh_model_catalog(force=True, app_settings=request_context.app_settings)
    send_text(token, chat_id, f"Model catalog refreshed: {refreshed} providers updated; {failed} failed.")
    send_models(
        token,
        chat_id,
        _catalog_current_model(db, chat_id, session, session_id, request_context=request_context),
        message_id=message_id,
        request_context=request_context,
        refresh_catalog=False,
    )
    return True


def _handle_provider_back(db, token, chat_id, session, session_id, request_context, message_id, send_models):
    send_models(
        token,
        chat_id,
        _catalog_current_model(db, chat_id, session, session_id, request_context=request_context),
        message_id=message_id,
        request_context=request_context,
    )
    return True


def _handle_provider_health_page(
    token,
    callback,
    answer_callback,
    data,
    chat_id,
    request_context,
    provider_policy: ProviderPolicy | None,
    message_id,
    send_health,
):
    parts = data.split(":")
    if len(parts) != 4 or not parts[3].isascii() or not parts[3].isdecimal() or len(parts[3]) > 4:
        answer_callback(token, str(callback.get("id", "")), "Panel expired; reopen /providers")
        return True
    report = load_provider_report(parts[2], chat_id, request_context=request_context)
    if report is None:
        answer_callback(token, str(callback.get("id", "")), "Panel expired; reopen /providers")
        return True
    answer_callback(token, str(callback.get("id", "")), "Status")
    send_health(
        token,
        chat_id,
        message_id,
        request_context=request_context,
        provider_policy=provider_policy,
        report=report,
        report_token=parts[2],
        page=int(parts[3]),
    )
    return True


def _handle_provider_maint(
    token,
    callback,
    answer_callback,
    data,
    chat_id,
    session,
    request_context,
    provider_policy: ProviderPolicy | None,
    message_id,
    send_models,
    send_health,
):
    parts = data.split(":")
    provider_id = (
        resolve_provider_action(parts[3], parts[2], chat_id, request_context=request_context)
        if len(parts) == 4 and parts[2] in {"refresh", "test", "reset"}
        else None
    )
    if provider_id is None:
        answer_callback(token, str(callback.get("id", "")), "Panel expired; reopen /providers")
        return True
    action = parts[2]
    answer_callback(token, str(callback.get("id", "")), "Running provider action")
    if action == "test":
        send_health(
            token,
            chat_id,
            message_id,
            request_context=request_context,
            provider_policy=provider_policy,
            provider_id=provider_id,
        )
        return True
    if action == "refresh":
        _config, refreshed, failed = refresh_model_catalog(
            force=True, provider_id=provider_id, app_settings=request_context.app_settings
        )
        send_text(token, chat_id, f"Model catalog refreshed: {refreshed} providers updated; {failed} failed.")
    elif provider_policy is not None:
        provider_policy.reset(provider_id)
        send_text(token, chat_id, "Local runtime state reset. Provider credentials and model selection are unchanged.")
    else:
        send_text(token, chat_id, "Runtime diagnostics are not available.")
    send_models(
        token,
        chat_id,
        session["model_id"] or request_context.app_settings.default_model,
        provider_id,
        message_id=message_id,
        request_context=request_context,
        refresh_catalog=False,
    )
    return True


def _handle_provider(
    db, token, callback, answer_callback, data, chat_id, session, session_id, request_context, message_id, send_models
):
    provider_id = resolve_dynamic_callback_token(data.split(":", 1)[1], "provider", chat_id, db=db) or ""
    answer_callback(token, str(callback.get("id", "")), "Provider selected")
    send_models(
        token,
        chat_id,
        _catalog_current_model(db, chat_id, session, session_id, request_context=request_context),
        provider_id,
        message_id=message_id,
        request_context=request_context,
    )
    return True


def _handle_unsupported(db, token, callback, answer_callback, data, chat_id):
    provider_id = resolve_dynamic_callback_token(data.split(":", 1)[1], "provider", chat_id, db=db) or ""
    answer_callback(token, str(callback.get("id", "")), "Catalog only: adapter not enabled")
    send_text(
        token,
        chat_id,
        f"Provider '{provider_id}' is visible in the bridge catalog, but its adapter is not enabled yet.",
    )
    return True


def _handle_modeltarget(
    db, token, callback, answer_callback, data, chat_id, session, session_id, request_context, message_id, send_models
):
    target = data.split(":", 1)[1]
    if target not in {"story", "utility"}:
        answer_callback(token, str(callback.get("id", "")), "Model target invalid")
        return True
    set_model_target_selection(db, chat_id, session_id, target)
    answer_callback(token, str(callback.get("id", "")), "Target selected")
    send_models(
        token,
        chat_id,
        _catalog_current_model(db, chat_id, session, session_id, request_context=request_context),
        message_id=message_id,
        request_context=request_context,
    )
    return True


def _handle_model(
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
    request_context,
    message_id,
):
    model = resolve_dynamic_callback_token(data.split(":", 1)[1], "model", chat_id, db=db) or ""
    if not model:
        answer_callback(token, str(callback.get("id", "")), "Model choice expired")
        return True
    target = get_model_target_selection(db, chat_id, session_id)
    if not target:
        answer_callback(token, str(callback.get("id", "")), "Choose Story or Utility first")
        send_model_target_menu(
            token,
            chat_id,
            _catalog_current_model(db, chat_id, session, session_id, request_context=request_context),
            task_model_for_session(db, chat_id, session, "utility", app_settings=request_context.app_settings),
            message_id,
            request_context=request_context,
        )
        return True
    if target == "story":
        update_session(
            db, chat_id, session_id, operation_id=operation_id, operation_kind="model_select", model_id=model
        )
        message = f"Story model updated: {model}"
    else:
        set_task_model(db, chat_id, session_id, model, "utility")
        message = f"Utility model updated: {model}"
    clear_model_target_selection(db, chat_id, session_id)
    answer_callback(token, str(callback.get("id", "")), "Model updated")
    send_text(token, chat_id, message)
    story_model = model if target == "story" else session["model_id"] or request_context.app_settings.default_model
    resolved_session = {**session, "model_id": story_model}
    send_model_target_menu(
        token,
        chat_id,
        story_model,
        task_model_for_session(db, chat_id, resolved_session, "utility", app_settings=request_context.app_settings),
        message_id,
        request_context=request_context,
    )
    return True


def _bind_routes(
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
    request_context,
    provider_policy: ProviderPolicy | None = None,
    provider_probes: ProviderProbes | None = None,
):
    """Bind the existing action vocabulary to narrowly scoped operations."""
    message_id = message.get("message_id")
    send_models = partial(send_model_menu, provider_policy=provider_policy)
    send_health = partial(send_provider_health_menu, provider_probes=provider_probes)
    return (
        {
            "models:target": lambda: _handle_models_target(
                db, token, callback, answer_callback, chat_id, session, session_id, request_context, message_id
            ),
            "models:cancel": lambda: _handle_models_cancel(db, token, callback, answer_callback),
            "models:story-reasoning": lambda: _handle_models_story_reasoning(
                token, callback, answer_callback, chat_id, request_context, message_id
            ),
            "models:utility-reasoning": lambda: _handle_models_utility_reasoning(
                token, callback, answer_callback, chat_id, request_context, message_id
            ),
            "models:back": lambda: _handle_models_back(
                db,
                token,
                callback,
                answer_callback,
                chat_id,
                session,
                session_id,
                request_context,
                message_id,
                send_models,
            ),
            "provider:health": lambda: _handle_provider_health(
                token, callback, answer_callback, chat_id, request_context, provider_policy, message_id, send_health
            ),
            "provider:refresh": lambda: _handle_provider_refresh(
                db,
                token,
                callback,
                answer_callback,
                chat_id,
                session,
                session_id,
                request_context,
                message_id,
                send_models,
            ),
            "provider:back": lambda: _handle_provider_back(
                db, token, chat_id, session, session_id, request_context, message_id, send_models
            ),
        },
        (
            (
                "models:providers:",
                lambda: _handle_models_providers(
                    db,
                    token,
                    callback,
                    answer_callback,
                    data,
                    chat_id,
                    session,
                    session_id,
                    request_context,
                    message_id,
                    send_models,
                ),
            ),
            (
                "models:model:",
                lambda: _handle_models_model(
                    db,
                    token,
                    callback,
                    answer_callback,
                    data,
                    chat_id,
                    session,
                    session_id,
                    request_context,
                    message_id,
                    send_models,
                ),
            ),
            (
                "storyreasoning:",
                lambda: _handle_storyreasoning(
                    db, token, callback, answer_callback, data, chat_id, session_id, request_context, message_id
                ),
            ),
            (
                "utilityreasoning:",
                lambda: _handle_utilityreasoning(
                    db, token, callback, answer_callback, data, chat_id, session_id, request_context, message_id
                ),
            ),
            (
                "provider:health-page:",
                lambda: _handle_provider_health_page(
                    token,
                    callback,
                    answer_callback,
                    data,
                    chat_id,
                    request_context,
                    provider_policy,
                    message_id,
                    send_health,
                ),
            ),
            (
                "provider:maint:",
                lambda: _handle_provider_maint(
                    token,
                    callback,
                    answer_callback,
                    data,
                    chat_id,
                    session,
                    request_context,
                    provider_policy,
                    message_id,
                    send_models,
                    send_health,
                ),
            ),
            (
                "provider:",
                lambda: _handle_provider(
                    db,
                    token,
                    callback,
                    answer_callback,
                    data,
                    chat_id,
                    session,
                    session_id,
                    request_context,
                    message_id,
                    send_models,
                ),
            ),
            ("unsupported:", lambda: _handle_unsupported(db, token, callback, answer_callback, data, chat_id)),
            (
                "modeltarget:",
                lambda: _handle_modeltarget(
                    db,
                    token,
                    callback,
                    answer_callback,
                    data,
                    chat_id,
                    session,
                    session_id,
                    request_context,
                    message_id,
                    send_models,
                ),
            ),
            (
                "model:",
                lambda: _handle_model(
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
                    request_context,
                    message_id,
                ),
            ),
        ),
    )


def handle_provider_model_callback(
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
    request_context,
    provider_policy: ProviderPolicy | None = None,
    provider_probes: ProviderProbes | None = None,
):
    """Select one explicit exact/prefix route without changing callback action policy."""
    exact, prefixes = _bind_routes(
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
        request_context=request_context,
        provider_policy=provider_policy,
        provider_probes=provider_probes,
    )
    handler = exact.get(data)
    if handler is None:
        handler = next((call for prefix, call in prefixes if data.startswith(prefix)), None)
    return handler() if handler is not None else False
