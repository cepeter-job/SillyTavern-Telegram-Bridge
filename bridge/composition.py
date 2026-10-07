"""Explicit startup configuration and root infrastructure composition."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from bridge.conversation_service import ConversationService
from bridge.delivery_port import DeliveryPort
from bridge.group_director_service import GroupDirectorService
from bridge.group_service import GroupService
from bridge.hindsight_recall_runtime import HindsightRecallRuntime
from bridge.input_flow_service import InputFlowService
from bridge.job_service import JobService
from bridge.memory_diagnostics import MemoryDiagnostics
from bridge.memory_service import MemoryService
from bridge.model_router import ModelRouter
from bridge.npc_service import NpcService
from bridge.persona_service import PersonaService
from bridge.port_contracts import BackgroundSubmit, ChatSubmit, DownloadFile, ProviderProbes, SendText, TelegramRequest
from bridge.provider_port import ProviderPort
from bridge.rag_service import RagService
from bridge.runtime_health import RuntimeHealth
from bridge.session_service import SessionService
from bridge.settings import AppSettings
from bridge.sync_service import SyncService


@dataclass(frozen=True)
class TelegramRuntime:
    request: TelegramRequest
    send_text: SendText
    download_file: DownloadFile


@dataclass(frozen=True)
class BackgroundRuntime:
    submit_chat: ChatSubmit
    submit: BackgroundSubmit
    register_backlog_dispatcher: Callable[[Callable[[], None]], None]
    begin_shutdown: Callable[[], None]


@dataclass(frozen=True)
class BridgeServices:
    config: AppSettings
    db_factory: Callable[[], sqlite3.Connection]
    telegram: TelegramRuntime
    background: BackgroundRuntime
    jobs: JobService
    conversation: ConversationService
    delivery: DeliveryPort
    group: GroupService
    session: SessionService
    group_director: GroupDirectorService
    input_flow: InputFlowService
    model_router: ModelRouter
    provider: ProviderPort
    rag: RagService
    memory: MemoryService
    npc: NpcService
    persona: PersonaService
    sync: SyncService
    memory_diagnostics: MemoryDiagnostics | None = None
    health: RuntimeHealth | None = None
    provider_probes: ProviderProbes | None = None
    hindsight_recall: HindsightRecallRuntime | None = None
