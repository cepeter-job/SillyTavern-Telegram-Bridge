"""Narrow structural ports for Light Novel adapters; no composition-root imports."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Protocol

from bridge.delivery_port import DeliveryPort
from bridge.job_service import JobService
from bridge.persona_service import PersonaService
from bridge.port_contracts import SendText, TelegramRequest
from bridge.provider_port import ProviderPort
from bridge.session_service import SessionService
from bridge.settings import AppSettings


class ChoiceTelegram(Protocol):
    @property
    def request(self) -> TelegramRequest: ...

    @property
    def send_text(self) -> SendText: ...


class ChoiceMemory(Protocol):
    def summary_status(self, db: sqlite3.Connection, chat_id: str, session_id: str) -> tuple[str, int]: ...


class ChoiceNpc(Protocol):
    def context_for_prompt(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session: dict[str, str],
        fields: dict[str, str],
        query: str,
        history_rows: list[tuple[str, str]],
        *,
        through_rowid: int | None = None,
    ) -> str: ...


class LightNovelRuntime(Protocol):
    @property
    def persona(self) -> PersonaService: ...

    @property
    def memory(self) -> ChoiceMemory: ...

    @property
    def npc(self) -> ChoiceNpc: ...

    @property
    def config(self) -> AppSettings: ...

    @property
    def db_factory(self) -> Callable[[], sqlite3.Connection]: ...

    @property
    def jobs(self) -> JobService: ...

    @property
    def session(self) -> SessionService: ...

    @property
    def delivery(self) -> DeliveryPort: ...

    @property
    def provider(self) -> ProviderPort: ...

    @property
    def telegram(self) -> ChoiceTelegram: ...
