"""Capability-aware image model selection and session-scoped routing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import yaml

from bridge.metadata import get_meta, set_meta
from bridge.settings import AppSettings

AUTO_IMAGE_SELECTION = "auto"
IMAGE_PROMPT_MAX_CHARS = 4000
IMAGE_MODEL_PROMPT_MAX_CHARS = {
    "z-image-turbo": 1200,
    "step-image-edit-2": 512,
}
IMAGE_DEFAULT_SIZE = "1024x1024"
IMAGE_SIZE_PRESETS = (
    ("Square", "1024x1024"),
    ("Landscape", "1536x1024"),
    ("Portrait", "1024x1536"),
)
_VALID_CAPABILITY_MODES = {"text", "reference", "both"}


@dataclass(frozen=True)
class ImageRoute:
    selection: str
    provider_id: str
    model: str
    transport: Literal["text", "reference"]
    edit_route: str | None
    spec: dict[str, object]


def _image_model_key(chat_id: str, session_id: str) -> str:
    return f"image_model:{chat_id}:{session_id}"


def _image_size_key(chat_id: str, session_id: str) -> str:
    return f"image_size:{chat_id}:{session_id}"


def _catalog(*, app_settings: AppSettings) -> dict[str, object]:
    if not app_settings.provider_config_file.exists():
        return {}
    raw = yaml.safe_load(app_settings.provider_config_file.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("Image provider catalog must be a mapping")
    return raw


def image_provider_specs(*, app_settings: AppSettings) -> list[tuple[str, dict[str, object], str]]:
    providers = _catalog(app_settings=app_settings).get("providers") or {}
    if not isinstance(providers, dict):
        raise ValueError("Image provider catalog providers must be a mapping")
    result: list[tuple[str, dict[str, object], str]] = []
    for provider_id, raw in providers.items():
        if not isinstance(raw, dict) or not raw.get("image_enabled"):
            continue
        spec = dict(raw)
        models = _declared_models(spec)
        if models:
            result.append((str(provider_id), spec, models[0]))
    return result


def _declared_models(spec: dict[str, object]) -> list[str]:
    raw = spec.get("image_models") or []
    if not isinstance(raw, list):
        raise ValueError("image_models must be a list")
    return [str(item) for item in raw if str(item)]


def _split_selection(selection: str, *, field_name: str = "image selection") -> tuple[str, str]:
    value = str(selection or "").strip()
    if "::" not in value:
        raise ValueError(f"{field_name} must be a fully-qualified provider::model selection")
    provider_id, model = value.split("::", 1)
    if not provider_id or not model:
        raise ValueError(f"{field_name} must be a fully-qualified provider::model selection")
    return provider_id, model


def _find_model(
    selection: str,
    *,
    app_settings: AppSettings,
) -> tuple[str, dict[str, object], str] | None:
    provider_id, model = _split_selection(selection)
    for candidate_id, spec, _default in image_provider_specs(app_settings=app_settings):
        if candidate_id == provider_id and model in _declared_models(spec):
            return candidate_id, spec, model
    return None


def _capability(spec: dict[str, object], model: str) -> tuple[str, str | None]:
    capabilities = spec.get("image_model_capabilities") or {}
    if not isinstance(capabilities, dict):
        raise ValueError("image_model_capabilities must be a mapping")
    raw = capabilities.get(model)
    if raw is None:
        return "text", None
    if not isinstance(raw, dict):
        raise ValueError(f"Image model capability for {model} must be a mapping")
    mode = str(raw.get("mode") or "text").strip().casefold()
    if mode not in _VALID_CAPABILITY_MODES:
        raise ValueError(f"Image model {model} has unsupported capability mode {mode!r}")
    edit_route = str(raw.get("edit_route") or "openai").strip() if mode in {"reference", "both"} else None
    if mode in {"reference", "both"} and not edit_route:
        edit_route = "openai"
    return mode, edit_route


def _concrete_route(
    selection: str,
    *,
    reference_available: bool,
    intended_transport: Literal["text", "reference"] | None = None,
    app_settings: AppSettings,
) -> ImageRoute:
    found = _find_model(selection, app_settings=app_settings)
    if found is None:
        raise ValueError(f"Image model {selection} is not declared by an enabled image provider")
    provider_id, spec, model = found
    mode, edit_route = _capability(spec, model)
    if intended_transport == "text":
        if mode not in {"text", "both"}:
            raise ValueError(f"Image model {selection} does not support text generation")
        transport: Literal["text", "reference"] = "text"
        edit_route = None
    elif intended_transport == "reference":
        if mode not in {"reference", "both"}:
            raise ValueError(f"Image model {selection} does not support reference generation")
        if not reference_available:
            raise ValueError(f"Image model {selection} requires a usable character reference")
        transport = "reference"
    elif mode == "reference":
        if not reference_available:
            raise ValueError(f"Image model {selection} requires a usable character reference")
        transport = "reference"
    elif mode == "both" and reference_available:
        transport = "reference"
    else:
        transport = "text"
        edit_route = None
    return ImageRoute(
        selection=f"{provider_id}::{model}",
        provider_id=provider_id,
        model=model,
        transport=transport,
        edit_route=edit_route,
        spec=spec,
    )


def _auto_config(*, app_settings: AppSettings) -> dict[str, object] | None:
    raw = _catalog(app_settings=app_settings).get("image_auto")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("image_auto must be a mapping")
    return dict(raw)


def _auto_target(config: dict[str, object], name: str) -> str:
    value = str(config.get(name) or "").strip()
    _split_selection(value, field_name=f"image_auto.{name}")
    return value


def _valid_auto_default(*, app_settings: AppSettings) -> bool:
    config = _auto_config(app_settings=app_settings)
    if config is None:
        return False
    text_target = _auto_target(config, "text_model")
    reference_target = _auto_target(config, "reference_model")
    _concrete_route(
        text_target,
        reference_available=False,
        intended_transport="text",
        app_settings=app_settings,
    )
    try:
        _concrete_route(
            reference_target,
            reference_available=True,
            intended_transport="reference",
            app_settings=app_settings,
        )
    except ValueError as exc:
        if "not declared" in str(exc):
            return False
        raise
    return True


def _default_concrete_selection(*, app_settings: AppSettings) -> str:
    specs = image_provider_specs(app_settings=app_settings)
    if not specs:
        raise ValueError(
            "No image provider is enabled; configure image_enabled and image_models in the private provider catalog"
        )
    provider_id, _spec, model = specs[0]
    return f"{provider_id}::{model}"


def image_model_options(
    *,
    app_settings: AppSettings,
    include_auto: bool = True,
) -> tuple[tuple[str, str], ...]:
    options: list[tuple[str, str]] = []
    if include_auto and _valid_auto_default(app_settings=app_settings):
        options.append((AUTO_IMAGE_SELECTION, "Auto"))
    for provider_id, spec, _default_model in image_provider_specs(app_settings=app_settings):
        provider_name = str(spec.get("name") or provider_id).strip() or provider_id
        for model in _declared_models(spec):
            options.append((f"{provider_id}::{model}", f"{provider_name} · {model}"[:64]))
    return tuple(options)


def session_image_settings(
    db,
    chat_id: str,
    session_id: str,
    *,
    app_settings: AppSettings,
) -> tuple[str, str]:
    concrete = {selection for selection, _label in image_model_options(app_settings=app_settings, include_auto=False)}
    requested = get_meta(db, _image_model_key(chat_id, session_id), "").strip()
    if requested == AUTO_IMAGE_SELECTION:
        config = _auto_config(app_settings=app_settings)
        if config is None:
            requested = ""
        else:
            text_target = _auto_target(config, "text_model")
            _concrete_route(
                text_target,
                reference_available=False,
                intended_transport="text",
                app_settings=app_settings,
            )
    elif requested not in concrete:
        requested = ""
    if not requested:
        requested = (
            AUTO_IMAGE_SELECTION
            if _valid_auto_default(app_settings=app_settings)
            else _default_concrete_selection(app_settings=app_settings)
        )
    allowed_sizes = {size for _label, size in IMAGE_SIZE_PRESETS}
    size = get_meta(db, _image_size_key(chat_id, session_id), "").strip()
    if size not in allowed_sizes:
        size = IMAGE_DEFAULT_SIZE
    return requested, size


def set_session_image_model(
    db,
    chat_id: str,
    session_id: str,
    selection: str,
    *,
    app_settings: AppSettings,
) -> str:
    value = str(selection or "").strip()
    if value == AUTO_IMAGE_SELECTION:
        if not _valid_auto_default(app_settings=app_settings):
            raise ValueError("Auto image routing is not fully configured")
    else:
        allowed = {item for item, _label in image_model_options(app_settings=app_settings, include_auto=False)}
        if value not in allowed:
            raise ValueError("Selected image model is no longer available")
    set_meta(db, _image_model_key(chat_id, session_id), value)
    return value


def set_session_image_size(db, chat_id: str, session_id: str, size: str) -> str:
    value = str(size or "").strip()
    allowed = {item for _label, item in IMAGE_SIZE_PRESETS}
    if value not in allowed:
        raise ValueError("Unsupported image size preset")
    set_meta(db, _image_size_key(chat_id, session_id), value)
    return value


def reset_session_image_settings(db, chat_id: str, session_id: str) -> None:
    set_meta(db, _image_model_key(chat_id, session_id), "")
    set_meta(db, _image_size_key(chat_id, session_id), "")


def resolve_image_route(
    selection: str,
    *,
    reference_available: bool,
    app_settings: AppSettings,
) -> ImageRoute:
    value = str(selection or "").strip()
    if not value:
        value = (
            AUTO_IMAGE_SELECTION
            if _valid_auto_default(app_settings=app_settings)
            else _default_concrete_selection(app_settings=app_settings)
        )
    if value != AUTO_IMAGE_SELECTION:
        return _concrete_route(value, reference_available=reference_available, app_settings=app_settings)

    config = _auto_config(app_settings=app_settings)
    if config is None:
        raise ValueError("Auto image routing is not configured")
    text_target = _auto_target(config, "text_model")
    text_route = _concrete_route(
        text_target,
        reference_available=False,
        intended_transport="text",
        app_settings=app_settings,
    )
    if not reference_available:
        return text_route

    reference_target = _auto_target(config, "reference_model")
    try:
        return _concrete_route(
            reference_target,
            reference_available=True,
            intended_transport="reference",
            app_settings=app_settings,
        )
    except ValueError as exc:
        if "not declared" in str(exc):
            return text_route
        raise


def image_prompt_max_chars(route: ImageRoute) -> int:
    return min(
        IMAGE_PROMPT_MAX_CHARS,
        IMAGE_MODEL_PROMPT_MAX_CHARS.get(route.model.casefold(), IMAGE_PROMPT_MAX_CHARS),
    )
