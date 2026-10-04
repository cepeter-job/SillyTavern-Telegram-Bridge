"""Per-call extension inputs; never bind one application's ports globally."""

import sqlite3
from dataclasses import dataclass

from bridge.persona_service import PersonaService
from bridge.provider_port import ProviderPort
from bridge.settings import AppSettings


@dataclass(frozen=True, slots=True)
class PostRetainContext:
    db: sqlite3.Connection
    chat_id: str
    session: dict[str, str]
    fields: dict[str, str]
    provider_port: ProviderPort
    app_settings: AppSettings
    persona_service: PersonaService | None = None
