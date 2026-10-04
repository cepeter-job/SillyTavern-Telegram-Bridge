"""Bounded versioned Director proposals; no database or transport ownership."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any

MAX_DIRECTOR_OUTPUT = 16384
DIRECTOR_TRANSITIONS = frozenset({"continue", "cut", "pov_switch", "time_jump", "thread_switch"})
DIRECTOR_POVS = frozenset({"first_person", "third_person_user", "third_person_rotating", "omniscient", "cinematic"})


class DirectorProposalError(ValueError):
    """A safe category for failures; never echo provider output into errors."""

    def __init__(self, category: str, message: str, *, repairable: bool = False):
        super().__init__(message)
        self.category = category
        self.repairable = repairable


@dataclass(frozen=True, slots=True)
class ProposedThread:
    thread_id: str
    title: str


@dataclass(frozen=True, slots=True)
class DirectorProposal:
    schema_version: int
    action: str
    expected_revision: int
    direction: str = ""
    scene_id: str = ""
    thread_id: str = ""
    viewpoint: str = ""
    pov: str = ""
    user_present: bool | None = None
    purpose: str = ""
    transition_type: str = "continue"
    location: str = ""
    time_scope: str = ""
    speaker: str = ""
    direction_ttl: int | None = None
    new_thread: ProposedThread | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _text(data: dict, field: str, maximum: int, *, identifier: bool = False) -> str:
    value = data.get(field, "")
    if not isinstance(value, str) or len(value) > maximum:
        raise DirectorProposalError("parse", "Director text field is invalid or oversized", repairable=True)
    value = value.strip()
    if identifier and value and not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise DirectorProposalError("parse", "Director identifier is invalid", repairable=True)
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DirectorProposalError("parse", "Director object has duplicate fields", repairable=True)
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise DirectorProposalError("parse", "Director output must contain finite JSON values", repairable=True)


def parse_director_proposal(raw: str) -> DirectorProposal:
    if not isinstance(raw, str) or len(raw) > MAX_DIRECTOR_OUTPUT:
        raise DirectorProposalError("size", "Director output exceeds the structured response limit")
    try:
        data = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise DirectorProposalError("parse", "Director returned malformed JSON", repairable=True) from exc
    if not isinstance(data, dict):
        raise DirectorProposalError("parse", "Director output must be one object", repairable=True)
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise DirectorProposalError("version", "Director schema version is missing or unsupported")
    if any(key in data for key in ("user_dialogue", "user_thoughts", "user_decision", "user_action")):
        raise DirectorProposalError("policy", "Director cannot author user decisions or dialogue")
    action = data.get("action")
    revision = data.get("expected_revision")
    if action not in ("continue", "transition_scene"):
        raise DirectorProposalError("parse", "Director action is missing or unsupported", repairable=True)
    if type(revision) is not int or not 0 <= revision < 2**63:
        raise DirectorProposalError("parse", "Director revision must be a nonnegative integer", repairable=True)
    present = data.get("user_present")
    if present is not None and type(present) is not bool:
        raise DirectorProposalError("parse", "Director presence must be boolean or null", repairable=True)
    ttl = data.get("direction_ttl")
    if ttl is not None and (type(ttl) is not int or not 1 <= ttl <= 1000):
        raise DirectorProposalError("parse", "Director direction lifetime must be 1 to 1000 turns", repairable=True)
    transition = _text(data, "transition_type", 30) or "continue"
    pov = _text(data, "pov", 40)
    if transition not in DIRECTOR_TRANSITIONS or (pov and pov not in DIRECTOR_POVS):
        raise DirectorProposalError("parse", "Director transition or viewpoint mode is invalid", repairable=True)
    proposed_thread = data.get("new_thread")
    new_thread = None
    if proposed_thread is not None:
        if not isinstance(proposed_thread, dict):
            raise DirectorProposalError("parse", "A proposed thread must be an object", repairable=True)
        thread_id = _text(proposed_thread, "thread_id", 100, identifier=True)
        title = _text(proposed_thread, "title", 200)
        if not thread_id or not title:
            raise DirectorProposalError("parse", "A proposed thread needs identity and title", repairable=True)
        new_thread = ProposedThread(thread_id, title)
    return DirectorProposal(
        schema_version=1,
        action=action,
        expected_revision=revision,
        direction=_text(data, "direction", 4000),
        scene_id=_text(data, "scene_id", 100, identifier=True),
        thread_id=_text(data, "thread_id", 100, identifier=True),
        viewpoint=_text(data, "viewpoint", 200),
        pov=pov,
        user_present=present,
        purpose=_text(data, "purpose", 1000),
        transition_type=transition,
        location=_text(data, "location", 300),
        time_scope=_text(data, "time_scope", 300),
        speaker=_text(data, "speaker", 200),
        direction_ttl=ttl,
        new_thread=new_thread,
    )
