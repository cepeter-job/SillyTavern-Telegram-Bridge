"""Pure adaptive cadence and material-event identities for narrative planning."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from bridge.narrative_values import NarrativeSettings, NarrativeState

DIRECTOR_LEASE_SECONDS = 600.0
DIRECTOR_FAILURE_BACKOFF_SECONDS = 30.0
ENDING_PHASES = frozenset({"resolution", "epilogue", "closed"})


def director_interval(settings: NarrativeSettings, phase: str) -> int:
    if settings.director_cadence_mode == "fixed":
        return settings.director_fixed_interval
    return {"setup": 10, "development": 6, "escalation": 4, "climax": 2}.get(phase, 6)


def director_event_key(
    state: NarrativeState,
    clock: Mapping[str, Any],
    threads: Sequence[Mapping[str, Any]] = (),
) -> str:
    material = [
        state.active_scene_id,
        state.active_thread_id,
        state.story_phase,
        state.viewpoint_character,
        state.pov_mode,
        state.user_present,
        clock.get("settings_revision", 0),
        clock.get("rewrite_revision", 0),
        sorted((str(row.get("thread_id", "")), str(row.get("status", ""))) for row in threads),
    ]
    return hashlib.sha256(json.dumps(material, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def director_due(
    current: Mapping[str, Any],
    state: NarrativeState,
    *,
    settings: NarrativeSettings,
    completed_turns: int,
    now: float,
    event: str = "",
    event_key: str = "",
) -> bool:
    if completed_turns <= 0 or state.story_phase in ENDING_PHASES:
        return False
    if current.get("inflight_token") and now - float(current.get("inflight_started_at", 0)) < DIRECTOR_LEASE_SECONDS:
        return False
    if (
        current.get("active_direction")
        and current.get("direction_source") == "user"
        and current.get("accepted_scene_id") == state.active_scene_id
    ):
        return False
    if event == "manual":
        return True
    if (
        current.get("degraded_state")
        and now - float(current.get("last_attempt_at", 0)) < DIRECTOR_FAILURE_BACKOFF_SECONDS
    ):
        return False
    if event in {"scene", "thread", "arc", "style", "rewrite", "major"}:
        return True
    previous = str(current.get("last_event_key", ""))
    if event_key and previous and event_key != previous:
        return True
    if current.get("active_direction") and int(current.get("direction_until_turn", 0)) <= completed_turns:
        return True
    return completed_turns - int(current.get("last_director_turn", 0)) >= director_interval(settings, state.story_phase)
