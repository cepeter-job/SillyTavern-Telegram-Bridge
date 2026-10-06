"""Authenticated active-session tracker inspection without state mutation."""

from contextlib import closing
from typing import Any

from bridge.miniapp_auth import MiniAppIdentity
from bridge.miniapp_types import ApiRoute
from bridge.simulation_view import active_tracker_view


def get_trackers(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    with closing(services.db_factory()) as db:
        return active_tracker_view(db, who.chat_id, app_settings=services.config)


def routes() -> list[ApiRoute]:
    return [ApiRoute("GET", "/trackers", get_trackers)]
