"""Sanitized model catalog and validated generation-setting application actions."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

from bridge.config import GENERATION_DEFAULTS
from bridge.generation_settings import (
    delete_generation_preset,
    get_generation_settings,
    load_generation_preset,
    preset_names,
    save_generation_preset,
    update_generation_settings,
)
from bridge.generation_settings_values import parse_generation_setting
from bridge.miniapp_auth import MiniAppIdentity
from bridge.miniapp_context import require_confirmation, session_scope, text
from bridge.miniapp_errors import MiniAppError
from bridge.miniapp_types import ApiRoute
from bridge.model_router import ModelRoutingError
from bridge.model_selection import set_task_model, task_model_for_session


def model_catalog(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    query = text(values, "q", 120, required=False).casefold()
    models = []
    try:
        catalog = services.model_router.load_catalog()
        if not isinstance(catalog, Mapping):
            raise ModelRoutingError("catalog")
        for provider, spec in catalog.items():
            if not isinstance(provider, str) or not isinstance(spec, Mapping):
                continue
            names = spec.get("models", [])
            if not isinstance(names, (list, tuple)):
                continue
            for name in names:
                if not isinstance(name, str) or len(name) > 200 or not name:
                    continue
                selection = f"{provider}::{name}"
                if query and query not in selection.casefold():
                    continue
                models.append({"id": selection, "provider": provider, "name": name})
                if len(models) >= 500:
                    break
            if len(models) >= 500:
                break
    except Exception:
        raise MiniAppError("Provider catalog is unavailable. Check the server configuration.", status=503) from None
    with session_scope(services, who, values) as scope:
        return {
            "models": models,
            "story": scope.session.get("model_id") or services.config.default_model,
            "utility": task_model_for_session(scope.db, scope.chat_id, scope.session, app_settings=services.config),
            "director": task_model_for_session(
                scope.db, scope.chat_id, scope.session, "director", app_settings=services.config
            ),
            "session": scope.session,
            "limit": 500,
        }


def select_model(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    target = text(values, "target", 20)
    model = text(values, "model", 200, required=target == "story")
    if target not in {"story", "utility", "director"}:
        raise MiniAppError("Choose Story, Utility, or Director model.")
    try:
        if model:
            resolved = services.model_router.route(model)
            model = f"{resolved.provider_id}::{resolved.model_id}"
    except ModelRoutingError:
        raise MiniAppError("Select a configured, unambiguous model.") from None
    with session_scope(services, who, values, write=True) as scope:
        if target == "story":
            services.session.update(scope.db, scope.chat_id, scope.session["session_id"], model_id=model)
        else:
            set_task_model(scope.db, scope.chat_id, scope.session["session_id"], model, target)
        return {"saved": True, "target": target, "model": model}


def validated_settings(payload: Any) -> dict[str, object]:
    if not isinstance(payload, dict) or not payload or not set(payload) <= set(GENERATION_DEFAULTS):
        raise MiniAppError("Submit only supported generation settings.")
    result = {}
    for key, value in payload.items():
        if key == "stop_sequences":
            if not isinstance(value, str) or len(value) > 404 or "\x00" in value:
                raise MiniAppError("Stop sequences must be text, at most four sequences of 100 characters.")
            value = value.replace("\n", ",")
        elif key in {"max_tokens", "reasoning_budget"}:
            if type(value) is not int:
                raise MiniAppError("Token counts must be whole numbers.")
        elif type(value) not in {int, float} or not math.isfinite(value):
            raise MiniAppError("Generation values must be finite numbers.")
        try:
            canonical, parsed = parse_generation_setting(key, str(value))
        except ValueError:
            raise MiniAppError(f"Invalid {key.replace('_', ' ')}; use the displayed limits.") from None
        result[canonical] = parsed
    return result


def generation(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    with session_scope(services, who, values) as scope:
        return {
            "settings": get_generation_settings(scope.db, scope.chat_id, scope.session["session_id"]),
            "session": scope.session,
        }


def save_generation(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    changes = validated_settings(values.get("settings"))
    with session_scope(services, who, values, write=True) as scope:
        return {"settings": update_generation_settings(scope.db, scope.chat_id, scope.session["session_id"], **changes)}


def _preset_name(values: dict) -> str:
    name = text(values, "name", 64)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise MiniAppError("Preset names use letters, numbers, hyphens and underscores.")
    return name


def presets(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    with session_scope(services, who, values) as scope:
        return {"presets": preset_names(scope.db, scope.chat_id)}


def save_preset(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    name = _preset_name(values)
    with session_scope(services, who, values, write=True) as scope:
        names = preset_names(scope.db, scope.chat_id)
        if name in names:
            require_confirmation(values)
        elif len(names) >= 100:
            raise MiniAppError("Preset limit reached. Remove an unused preset first.")
        settings = validated_settings(get_generation_settings(scope.db, scope.chat_id, scope.session["session_id"]))
        save_generation_preset(scope.db, scope.chat_id, name, settings)
        return {"saved": True}


def apply_preset(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    name = _preset_name(values)
    with session_scope(services, who, values, write=True) as scope:
        settings = load_generation_preset(scope.db, scope.chat_id, name)
        changes = validated_settings(settings)
        return {"settings": update_generation_settings(scope.db, scope.chat_id, scope.session["session_id"], **changes)}


def delete_preset(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    require_confirmation(values)
    with session_scope(services, who, values, write=True) as scope:
        return {"deleted": delete_generation_preset(scope.db, scope.chat_id, _preset_name(values))}


def routes() -> list[ApiRoute]:
    return [
        ApiRoute("GET", "/models", model_catalog),
        ApiRoute("POST", "/models", select_model),
        ApiRoute("GET", "/generation", generation),
        ApiRoute("PATCH", "/generation", save_generation),
        ApiRoute("GET", "/generation/presets", presets),
        ApiRoute("POST", "/generation/presets", save_preset),
        ApiRoute("POST", "/generation/presets/{name}/apply", apply_preset),
        ApiRoute("DELETE", "/generation/presets/{name}", delete_preset),
    ]
