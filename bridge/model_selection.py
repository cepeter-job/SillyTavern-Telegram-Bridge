"""Canonical model selection owner."""

from __future__ import annotations

import json
import re
import sqlite3
import time

import bridge.limits as _limits
from bridge.metadata import get_meta, set_meta
from bridge.model_router import ModelRouter, ModelRoutingError
from bridge.provider_catalog import load_routing_catalog
from bridge.settings import AppSettings


def task_model_key(chat_id: str, session_id: str, task: str = "utility") -> str:
    task_name = re.sub(r"[^a-z0-9_-]+", "-", str(task or "utility").casefold()).strip("-") or "utility"
    return f"task_model:{task_name}:{chat_id}:{session_id}"


def _catalog_route_state(model: str, *, app_settings: AppSettings) -> bool | None:
    """Return routability when a usable catalog exists; otherwise preserve the stored selection."""
    if not model:
        return False
    if not app_settings.provider_config_file.is_file():
        return None
    catalog = load_routing_catalog(app_settings=app_settings)
    if not catalog:
        return None
    try:
        ModelRouter(lambda: catalog).route(model)
    except ModelRoutingError:
        return False
    return True


def task_model_for_session(
    db: sqlite3.Connection, chat_id: str, session: dict[str, str], task: str = "utility", *, app_settings: AppSettings
) -> str:
    """Resolve a task model, skipping persisted selections no longer routable in the live catalog."""
    session_id = str(session["session_id"])
    task_name = str(task or "utility").casefold()
    candidates: list[str] = []

    def add(value: object) -> None:
        model = str(value or "").strip()
        if model and model not in candidates:
            candidates.append(model)

    add(get_meta(db, task_model_key(chat_id, session_id, task_name), ""))
    if task_name != "utility":
        add(get_meta(db, task_model_key(chat_id, session_id, "utility"), ""))
    add(session.get("model_id"))
    add(app_settings.default_model)
    if not candidates:
        return ""

    for candidate in candidates:
        state = _catalog_route_state(candidate, app_settings=app_settings)
        if state is None:
            return candidates[0]
        if state:
            return candidate
    return candidates[0]


def set_task_model(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    model: str,
    task: str = "utility",
) -> str:
    value = str(model or "").strip()
    if value.casefold() in {"main", "default", "off", "inherit"}:
        value = ""
    if value and (len(value) > 200 or any(ch.isspace() for ch in value)):
        raise ValueError("model id must be at most 200 characters and contain no whitespace")
    set_meta(db, task_model_key(chat_id, session_id, task), value)
    return value


def utility_reasoning_key(chat_id: str, session_id: str) -> str:
    return f"utility_reasoning:{chat_id}:{session_id}"


def utility_reasoning_for_session(db: sqlite3.Connection, chat_id: str, session_id: str) -> int:
    raw = get_meta(db, utility_reasoning_key(chat_id, session_id), "0")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0
    return value if 0 <= value <= 32000 else 0


def set_utility_reasoning(db: sqlite3.Connection, chat_id: str, session_id: str, budget: int) -> int:
    value = int(budget)
    if not 0 <= value <= 32000:
        raise ValueError("utility reasoning budget must be between 0 and 32000")
    set_meta(db, utility_reasoning_key(chat_id, session_id), str(value))
    return value


def director_reasoning_key(chat_id: str, session_id: str) -> str:
    return f"director_reasoning:{chat_id}:{session_id}"


def director_reasoning_for_session(db: sqlite3.Connection, chat_id: str, session_id: str) -> int:
    raw = get_meta(db, director_reasoning_key(chat_id, session_id), "0")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0
    return value if 0 <= value <= 32000 else 0


def set_director_reasoning(db: sqlite3.Connection, chat_id: str, session_id: str, budget: int) -> int:
    if type(budget) is not int or not 0 <= budget <= 32000:
        raise ValueError("director reasoning budget must be an integer between 0 and 32000")
    set_meta(db, director_reasoning_key(chat_id, session_id), str(budget))
    return budget


def model_target_selection_key(chat_id: str, session_id: str) -> str:
    return f"model_target_selection:{chat_id}:{session_id}"


def set_model_target_selection(db: sqlite3.Connection, chat_id: str, session_id: str, target: str) -> None:
    if target not in {"story", "utility", "director"}:
        raise ValueError("invalid model target")
    set_meta(
        db,
        model_target_selection_key(chat_id, session_id),
        json.dumps({"target": target, "expires_at": time.time() + _limits.PENDING_SETTINGS_TTL_SECONDS}),
    )


def get_model_target_selection(db: sqlite3.Connection, chat_id: str, session_id: str) -> str:
    raw = get_meta(db, model_target_selection_key(chat_id, session_id), "")
    try:
        state = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return ""
    if float(state.get("expires_at", 0)) < time.time():
        set_meta(db, model_target_selection_key(chat_id, session_id), "")
        return ""
    target = str(state.get("target") or "")
    return target if target in {"story", "utility", "director"} else ""


def clear_model_target_selection(db: sqlite3.Connection, chat_id: str, session_id: str) -> None:
    set_meta(db, model_target_selection_key(chat_id, session_id), "")
