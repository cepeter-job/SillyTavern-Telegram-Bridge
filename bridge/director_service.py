"""Constrained planning orchestration; provider requests never hold write transactions."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any

from bridge.director_cadence import DIRECTOR_LEASE_SECONDS, director_event_key, director_interval
from bridge.director_contracts import DirectorProposal, DirectorProposalError, parse_director_proposal
from bridge.director_guidance import active_director_plan
from bridge.director_prompt import DIRECTOR_INSTRUCTION, build_director_input
from bridge.director_repository import (
    append_director_decision,
    claim_director_run,
    director_ending_lifecycle,
    director_story_turns,
    load_director_state,
    mark_director_degraded,
    publish_director_plan,
    release_director_run,
    write_manual_direction,
    write_manual_objective,
)
from bridge.director_validation import validate_director_proposal
from bridge.generation_settings import get_generation_settings
from bridge.model_selection import director_reasoning_for_session, task_model_for_session
from bridge.narrative_context import load_narrative_state, narrative_clock_is_current
from bridge.narrative_policy import narrative_policy
from bridge.narrative_reconciliation import ensure_narrative_state_current
from bridge.narrative_repository import list_narrative_threads, load_narrative_clock, load_narrative_thread
from bridge.narrative_settings import load_session_narrative_settings
from bridge.persona_service import PersonaService
from bridge.provider_errors import ProviderRequestError
from bridge.provider_port import ProviderPort
from bridge.settings import AppSettings
from bridge.sqlite_store import write_transaction

_TERMINAL = frozenset({"resolution_committed", "epilogue_pending", "epilogue_committed", "closed"})


@dataclass(frozen=True, slots=True)
class DirectorDecision:
    decision_id: int | None
    expected_revision: int
    source: str
    result: str
    reason: str = ""
    proposal: DirectorProposal | None = None


def _rejected(revision: int, reason: str, source: str = "ai") -> DirectorDecision:
    return DirectorDecision(None, revision, source, "rejected", reason)


def _record_failure(
    db: sqlite3.Connection, chat: str, session: str, token: str, source: dict[str, Any], revision: int, category: str
) -> DirectorDecision:
    message = f"Director could not update ({category}). The saved story and accepted direction are unchanged."
    decision_id = None
    with write_transaction(db):
        if (
            load_narrative_clock(db, chat, session) is not None
            and director_ending_lifecycle(db, chat, session) not in _TERMINAL
        ):
            if mark_director_degraded(
                db,
                chat,
                session,
                token=token,
                expected_director_revision=source["state_revision"],
                category=category,
                story_turn=director_story_turns(db, chat, session),
                now=time.time(),
            ):
                decision_id = append_director_decision(
                    db,
                    chat,
                    session,
                    source="ai",
                    result="rejected",
                    expected_revision=revision,
                    proposal_json="{}",
                    accepted_direction="",
                    reason=message,
                    created_at=time.time(),
                )
    logging.warning("Director update not published: %s", category)
    return DirectorDecision(decision_id, revision, "ai", "rejected", message)


class DirectorService:
    """One validated planning authority, shared by automatic and user-invoked controls."""

    def reassess(
        self,
        db: sqlite3.Connection,
        api_key: str,
        chat_id: str,
        session: dict[str, str],
        *,
        provider_port: ProviderPort,
        app_settings: AppSettings,
        reason: str,
        valid_characters: set[str] | None = None,
        user_characters: set[str] | None = None,
        persona_service: PersonaService | None = None,
    ) -> DirectorDecision:
        if db.in_transaction:
            raise RuntimeError("Director cannot call a provider inside a transaction")
        session_id = session["session_id"]
        clock = load_narrative_clock(db, chat_id, session_id)
        if clock is None:
            return _rejected(0, "This story no longer exists.")
        if director_ending_lifecycle(db, chat_id, session_id) in _TERMINAL:
            return _rejected(clock["state_revision"], "The story is closing or has ended.")
        if not clock["latest_rowid"]:
            return _rejected(
                clock["state_revision"], "Start the story before asking the Director to choose its next scene."
            )
        active = active_director_plan(db, chat_id, session_id)
        if active and active["direction_source"] == "user":
            return DirectorDecision(
                None, clock["state_revision"], "user", "accepted", "Current manual scene direction remains active."
            )
        token = uuid.uuid4().hex
        with write_transaction(db):
            if not claim_director_run(db, chat_id, session_id, token, time.time(), lease=DIRECTOR_LEASE_SECONDS):
                return _rejected(clock["state_revision"], "A Director reassessment is already running.")
        source = load_director_state(db, chat_id, session_id)
        revision = clock["state_revision"]
        try:
            state = ensure_narrative_state_current(
                db,
                api_key,
                chat_id,
                session,
                clock["latest_rowid"],
                provider_port=provider_port,
                app_settings=app_settings,
            )
            clock = load_narrative_clock(db, chat_id, session_id)
            if (
                clock is None
                or not narrative_clock_is_current(clock)
                or clock["state_revision"] != state.state_revision
                or clock["updated_through_rowid"] != state.updated_through_rowid
            ):
                return _rejected(revision, "This story changed during reconciliation.")
            revision = state.state_revision
            source = load_director_state(db, chat_id, session_id)
            if source["inflight_token"] != token or director_ending_lifecycle(db, chat_id, session_id) in _TERMINAL:
                return _rejected(revision, "This Director operation was superseded during reconciliation.")
            settings = load_session_narrative_settings(db, chat_id, session_id)
            data, cast, users, threads = build_director_input(
                db,
                chat_id,
                session,
                state,
                settings,
                source,
                app_settings=app_settings,
                valid_characters=valid_characters,
                user_characters=user_characters,
                persona_service=persona_service,
            )
            data["reassessment_reason"] = str(reason)[:100]
            messages = [
                {"role": "system", "content": DIRECTOR_INSTRUCTION},
                {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
            ]
            generation = get_generation_settings(db, chat_id, session_id)
            generation.update(
                temperature=0.2,
                max_tokens=2000,
                stop_sequences="",
                reasoning_budget=director_reasoning_for_session(db, chat_id, session_id),
            )
            model = task_model_for_session(db, chat_id, session, "director", app_settings=app_settings)
            port = provider_port.for_usage(chat_id, session_id, "director")
            proposal = None
            for attempt in range(2):
                if (
                    load_director_state(db, chat_id, session_id)["inflight_token"] != token
                    or load_narrative_clock(db, chat_id, session_id) != clock
                    or director_ending_lifecycle(db, chat_id, session_id) in _TERMINAL
                ):
                    return _rejected(revision, "This Director request was superseded.")
                raw = port.generate(
                    api_key,
                    model,
                    messages,
                    settings=generation,
                    session_id=f"director:{chat_id}:{session_id}",
                    force_non_stream=True,
                )
                try:
                    proposal = parse_director_proposal(raw)
                    if proposal.thread_id and load_narrative_thread(db, chat_id, session_id, proposal.thread_id):
                        threads.add(proposal.thread_id)
                    validate_director_proposal(
                        proposal,
                        policy=narrative_policy(settings),
                        state=state,
                        valid_characters=cast,
                        user_characters=users,
                        valid_threads=threads,
                        ending_state=director_ending_lifecycle(db, chat_id, session_id),
                    )
                    break
                except DirectorProposalError as exc:
                    if attempt or not exc.repairable:
                        raise
                    messages += [
                        {"role": "assistant", "content": raw[:16384]},
                        {
                            "role": "user",
                            "content": "Repair once: return a valid schema_version 1 JSON proposal "
                            "using the original expected_revision and allowed references. " + str(exc),
                        },
                    ]
            if proposal is None:
                raise DirectorProposalError("parse", "Director returned no valid proposal")
            now = time.time()
            turns = director_story_turns(db, chat_id, session_id)
            default_ttl = director_interval(settings, state.story_phase)
            ttl = min(proposal.direction_ttl or default_ttl, default_ttl)
            encoded = json.dumps(proposal.to_dict(), ensure_ascii=False, separators=(",", ":"))
            event_key = director_event_key(state, clock, list_narrative_threads(db, chat_id, session_id, limit=64))
            with write_transaction(db):
                if (
                    load_narrative_clock(db, chat_id, session_id) != clock
                    or director_ending_lifecycle(db, chat_id, session_id) in _TERMINAL
                ):
                    return _rejected(revision, "Director proposal became stale before publication.")
                if not publish_director_plan(
                    db,
                    chat_id,
                    session_id,
                    token=token,
                    expected_director_revision=source["state_revision"],
                    proposal_json=encoded,
                    direction=proposal.direction or proposal.purpose,
                    through_rowid=state.updated_through_rowid,
                    rewrite_revision=clock["rewrite_revision"],
                    settings_revision=clock["settings_revision"],
                    scene_id=state.active_scene_id,
                    story_turn=turns,
                    until_turn=turns + ttl,
                    event_key=event_key,
                    now=now,
                ):
                    return _rejected(revision, "A newer Director or manual decision won publication.")
                decision_id = append_director_decision(
                    db,
                    chat_id,
                    session_id,
                    source="ai",
                    result="accepted",
                    expected_revision=revision,
                    proposal_json=encoded,
                    accepted_direction=proposal.direction or proposal.purpose,
                    reason=str(reason)[:100],
                    created_at=now,
                )
            return DirectorDecision(decision_id, revision, "ai", "accepted", "Direction updated.", proposal)
        except (ValueError, RuntimeError) as exc:
            category = exc.category if isinstance(exc, (DirectorProposalError, ProviderRequestError)) else "unavailable"
            return _record_failure(db, chat_id, session_id, token, source, revision, category)
        finally:
            with write_transaction(db):
                release_director_run(db, chat_id, session_id, token)

    def accept_manual_direction(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session_id: str,
        *,
        direction: str,
        scope: str,
        expected_revision: int,
        expected_director_revision: int,
    ) -> DirectorDecision:
        if scope not in {"next_scene", "persistent"} or not isinstance(direction, str) or len(direction) > 4000:
            raise ValueError("Choose a valid scope and a direction of at most 4,000 characters.")
        if type(expected_revision) is not int or type(expected_director_revision) is not int:
            raise ValueError("Director revisions must be integers.")
        direction = direction.strip()
        with write_transaction(db):
            clock = load_narrative_clock(db, chat_id, session_id)
            if clock is None or clock["state_revision"] != expected_revision:
                return _rejected(expected_revision, "The story changed. Reopen Director Room.", "user")
            if director_ending_lifecycle(db, chat_id, session_id) in _TERMINAL:
                return _rejected(expected_revision, "The story is closing or has ended.", "user")
            if scope == "next_scene" and direction and not narrative_clock_is_current(clock):
                return _rejected(
                    expected_revision,
                    "Scene continuity is catching up. Reassess before setting a scene direction.",
                    "user",
                )
            state = load_narrative_state(db, chat_id, session_id)
            if scope == "next_scene" and direction and not state.active_scene_id:
                return _rejected(
                    expected_revision,
                    "Start the story before directing a scene; a persistent objective can be set now.",
                    "user",
                )
            proposal = DirectorProposal(
                1,
                "continue",
                expected_revision,
                direction=direction,
                scene_id=state.active_scene_id,
                thread_id=state.active_thread_id,
            )
            encoded = json.dumps(proposal.to_dict(), ensure_ascii=False, separators=(",", ":"))
            now = time.time()
            if scope == "persistent":
                written = write_manual_objective(
                    db, chat_id, session_id, direction, expected_director_revision=expected_director_revision, now=now
                )
            else:
                written = write_manual_direction(
                    db,
                    chat_id,
                    session_id,
                    direction,
                    expected_director_revision=expected_director_revision,
                    proposal_json=encoded,
                    scene_id=state.active_scene_id,
                    through_rowid=state.updated_through_rowid,
                    rewrite_revision=clock["rewrite_revision"],
                    settings_revision=clock["settings_revision"],
                    now=now,
                )
            if not written:
                return _rejected(expected_revision, "A newer manual decision already changed Director Room.", "user")
            decision_id = append_director_decision(
                db,
                chat_id,
                session_id,
                source="user",
                result="accepted",
                expected_revision=expected_revision,
                proposal_json=encoded,
                accepted_direction=direction,
                reason="Persistent objective" if scope == "persistent" else "One-scene manual direction",
                created_at=now,
            )
            return DirectorDecision(
                decision_id, expected_revision, "user", "accepted", "Manual direction updated.", proposal
            )

    def accept_manual_transition(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session_id: str,
        proposal: DirectorProposal,
        *,
        expected_director_revision: int,
        valid_characters: set[str],
        user_characters: set[str],
        valid_threads: set[str],
    ) -> DirectorDecision:
        """Steer a future scene without changing reconciled story facts."""
        with write_transaction(db):
            clock = load_narrative_clock(db, chat_id, session_id)
            if not clock or not narrative_clock_is_current(clock):
                raise ValueError("Scene continuity is catching up. Reassess before choosing a thread.")
            state = load_narrative_state(db, chat_id, session_id)
            validate_director_proposal(
                proposal,
                policy=narrative_policy(load_session_narrative_settings(db, chat_id, session_id)),
                state=state,
                valid_characters=valid_characters,
                user_characters=user_characters,
                valid_threads=valid_threads,
                ending_state=director_ending_lifecycle(db, chat_id, session_id),
            )
            if proposal.action != "transition_scene":
                raise ValueError("Choose a valid scene transition.")
            now = time.time()
            encoded = json.dumps(proposal.to_dict(), ensure_ascii=False, separators=(",", ":"))
            if not write_manual_direction(
                db,
                chat_id,
                session_id,
                proposal.direction or proposal.purpose,
                expected_director_revision=expected_director_revision,
                proposal_json=encoded,
                scene_id=state.active_scene_id,
                through_rowid=state.updated_through_rowid,
                rewrite_revision=clock["rewrite_revision"],
                settings_revision=clock["settings_revision"],
                now=now,
            ):
                raise ValueError("A newer Director decision already changed this plan.")
            decision_id = append_director_decision(
                db,
                chat_id,
                session_id,
                source="user",
                result="accepted",
                expected_revision=state.state_revision,
                proposal_json=encoded,
                accepted_direction=proposal.direction or proposal.purpose,
                reason="One-scene thread selection",
                created_at=now,
            )
            return DirectorDecision(
                decision_id, state.state_revision, "user", "accepted", "Next thread selected.", proposal
            )
