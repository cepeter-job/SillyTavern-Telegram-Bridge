"""Construct and close the SDK without mixing transport ownership with memory state."""

from __future__ import annotations

import logging
from typing import Any

from bridge.hindsight_endpoint import prepare_compatible_hindsight_endpoint
from bridge.settings import AppSettings


def hindsight_client(*, app_settings: AppSettings, request_timeout: float = 30.0) -> Any:
    base_url = prepare_compatible_hindsight_endpoint(app_settings)

    from hindsight_client import Hindsight

    api_key = app_settings.environ.get("HINDSIGHT_API_KEY") or None
    return Hindsight(
        base_url=base_url, api_key=api_key, timeout=request_timeout, user_agent="SillyTavernTelegramBridge/1.0"
    )


def close_hindsight_client(client: Any) -> None:
    """Close the supported SDK wrapper on its own synchronous lifecycle."""
    if client is None:
        return
    try:
        client.close()
    except Exception:
        logging.debug("Could not close Hindsight client cleanly", exc_info=True)
