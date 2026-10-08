"""Compose local story readers with one explicitly owned optional recall runtime."""

from __future__ import annotations

import logging
from functools import partial

from bridge.context_selection_store import prepare_context_selection
from bridge.hindsight_endpoint import prepare_hindsight_endpoint
from bridge.hindsight_recall_runtime import HindsightRecallRuntime
from bridge.memory import get_session_summary, purge_hindsight_session
from bridge.memory_artifact_store import read_scene_block, read_summary_block
from bridge.memory_backend import recall_memory_results, recall_scoped_memory
from bridge.memory_retirement_store import queue_session_memory_cleanup
from bridge.memory_scope_runtime import resolve_session_memory_scope
from bridge.memory_scope_store import read_episodic_block, validate_memory_blocks
from bridge.memory_service import MemoryService
from bridge.port_contracts import GroupStateRead, RetainSessionMemory
from bridge.settings import AppSettings


def build_memory_service(
    app_settings: AppSettings, *, load_group_state: GroupStateRead, retain_session: RetainSessionMemory
) -> tuple[MemoryService, HindsightRecallRuntime | None]:
    runtime = None
    try:
        base_url, _host = prepare_hindsight_endpoint(app_settings)
        runtime = HindsightRecallRuntime(
            base_url=base_url, api_key=app_settings.environ.get("HINDSIGHT_API_KEY") or None
        )
    except Exception:
        logging.warning("Optional Hindsight recall configuration unavailable; local memory remains active")
    remote_recall = runtime.recall if runtime is not None else None
    return MemoryService(
        resolve_scope=partial(
            resolve_session_memory_scope, app_settings=app_settings, load_group_state=load_group_state
        ),
        scoped_recall=partial(recall_scoped_memory, app_settings=app_settings, remote_recall=remote_recall),
        scoped_episodes=read_episodic_block,
        scoped_summary=read_summary_block,
        scoped_scene=read_scene_block,
        validate_blocks=validate_memory_blocks,
        summary_state=get_session_summary,
        retain_session=retain_session,
        queue_session_cleanup=queue_session_memory_cleanup,
        purge_session_memory=partial(purge_hindsight_session, app_settings=app_settings),
        search_backend=partial(recall_memory_results, app_settings=app_settings, remote_recall=remote_recall),
        select_context=partial(
            prepare_context_selection, app_settings=app_settings, validate_blocks=validate_memory_blocks
        ),
    ), runtime
