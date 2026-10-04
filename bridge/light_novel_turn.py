"""Per-generation Light Novel envelope state shared by narrative generation paths."""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass

from bridge.job_store import job_actor_id
from bridge.light_novel_format import add_inline_contract, light_novel_response_shape, parse_story_response_diagnostic
from bridge.light_novel_repository import ChoiceSet
from bridge.light_novel_service import attach_turn, prepare_turn
from bridge.narrative_context import narrative_context_for_session
from bridge.sqlite_store import write_transaction


@dataclass
class NovelTurn:
    record: ChoiceSet
    choices: list[str] | None = None
    choice_policy: str = ""

    def messages(self, messages: list[dict], language: str) -> list[dict]:
        if self.record.strategy != "a":
            return messages
        return add_inline_contract(messages, self.record.requested_count, language, narrative_policy=self.choice_policy)

    def extract(self, raw: str) -> str:
        if self.record.strategy != "a":
            return raw
        try:
            story, self.choices, reason, observed_count = parse_story_response_diagnostic(
                raw, self.record.requested_count
            )
        except ValueError as exc:
            detail = str(exc)
            reason = (
                "no_usable_narrative"
                if detail == "Story response has no usable narrative"
                else "malformed_story_envelope"
                if detail == "Malformed story envelope; no complete narrative to recover"
                else "invalid_story_protocol"
            )
            logging.warning(
                "Light Novel story protocol rejected: strategy=%s shape=%s chars=%s requested_count=%s reason=%s",
                self.record.strategy,
                light_novel_response_shape(raw),
                len(str(raw or "")),
                self.record.requested_count,
                reason,
            )
            raise
        if self.choices is None:
            logging.warning(
                "Light Novel inline choices unavailable: reason=%s requested_count=%s observed_count=%s",
                reason or "unknown",
                self.record.requested_count,
                observed_count,
            )
        return story

    def finalize(self, rendered: str) -> str:
        """Remove protocol text reintroduced by model-backed visible-response rewrites."""
        if self.record.strategy != "a":
            return rendered
        story, choices, _reason, _observed_count = parse_story_response_diagnostic(
            rendered, self.record.requested_count
        )
        if self.choices is None and choices is not None:
            self.choices = choices
        return story

    def commit(self, db: sqlite3.Connection, assistant_rowid: int, story: str) -> None:
        # Missing/invalid inline choices stay pending. attach_turn() already
        # schedules the durable choice-only worker, which performs one automatic
        # same-strategy recovery pass and owns the final ready/failed transition.
        with write_transaction(db):
            attach_turn(db, self.record, assistant_rowid, story, self.choices)


def begin_novel_turn(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict,
    kind: str,
    operation_id: int | str | None,
) -> NovelTurn | None:
    identity = str(operation_id) if operation_id is not None else f"direct-{time.time_ns()}"
    actor_id = str(session.get("_actor_id") or "")
    if not actor_id and isinstance(operation_id, int):
        actor_id = job_actor_id(db, operation_id)
    record = prepare_turn(db, chat_id, session, f"{kind}:{identity}", actor_id)
    return (
        NovelTurn(record, choice_policy=narrative_context_for_session(db, chat_id, session["session_id"], "choices"))
        if record is not None
        else None
    )
