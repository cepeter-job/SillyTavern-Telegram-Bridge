"""Bounded, descriptive Utility output for committed narrative facts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from bridge.narrative_arcs import NarrativeArc, parse_arc_updates
from bridge.narrative_values import NarrativeScene, NarrativeThread

MAX_RECONCILIATION_OUTPUT = 24000
MAX_RECONCILIATION_INPUT = 28000
MAX_THREAD_UPDATES = 8


@dataclass(frozen=True, slots=True)
class NarrativeExtraction:
    story_phase: str
    scene: NarrativeScene
    threads: tuple[NarrativeThread, ...]
    arcs: tuple[NarrativeArc, ...] = ()


def _text(value: Any, field: str, maximum: int, *, required: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise ValueError(f"Invalid narrative {field}")
    return value.strip()


def _identifier(value: Any, field: str) -> str:
    value = _text(value, field, 100, required=True)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}", value) is None:
        raise ValueError(f"Invalid narrative {field}")
    return value


def _choice(value: Any, field: str, allowed: set[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"Invalid narrative {field}")
    return value


def parse_narrative_extraction(raw: str) -> NarrativeExtraction:
    if not isinstance(raw, str) or len(raw) > MAX_RECONCILIATION_OUTPUT:
        raise ValueError("Narrative extraction exceeds its size limit")
    data = json.loads(raw)
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("Unsupported narrative extraction version")
    phase = _choice(data.get("story_phase"), "phase", {"setup", "development", "escalation", "climax", "resolution"})
    scene = data.get("scene")
    threads = data.get("threads")
    if not isinstance(scene, dict) or not isinstance(threads, list) or len(threads) > MAX_THREAD_UPDATES:
        raise ValueError("Invalid narrative scene or thread updates")
    present = scene.get("user_present")
    if present is not None and type(present) is not bool:
        raise ValueError("Invalid narrative user presence")
    parsed_scene = NarrativeScene(
        scene_id=_identifier(scene.get("scene_id"), "scene identifier"),
        thread_id=_identifier(scene.get("thread_id"), "thread identifier"),
        viewpoint_character=_text(scene.get("viewpoint_character", ""), "viewpoint", 200),
        pov_mode=_choice(
            scene.get("pov_mode"),
            "POV",
            {
                "first_person",
                "third_person_user",
                "third_person_rotating",
                "omniscient",
                "cinematic",
            },
        ),
        user_present=present,
        purpose=_text(scene.get("purpose", ""), "scene purpose", 1000),
        transition_type=_choice(
            scene.get("transition_type"),
            "transition",
            {
                "continue",
                "cut",
                "pov_switch",
                "time_jump",
                "thread_switch",
            },
        ),
    )
    parsed_threads = []
    seen = set()
    for thread in threads:
        if not isinstance(thread, dict):
            raise ValueError("Invalid narrative thread")
        identifier = _identifier(thread.get("thread_id"), "thread identifier")
        if identifier in seen:
            raise ValueError("Duplicate narrative thread identifier")
        seen.add(identifier)
        parsed_threads.append(
            NarrativeThread(
                thread_id=identifier,
                title=_text(thread.get("title"), "thread title", 200, required=True),
                status=_choice(thread.get("status"), "thread status", {"active", "offscreen", "dormant", "resolved"}),
                summary=_text(thread.get("summary", ""), "thread summary", 2000),
            )
        )
    return NarrativeExtraction(phase, parsed_scene, tuple(parsed_threads), parse_arc_updates(data.get("arcs", [])))
