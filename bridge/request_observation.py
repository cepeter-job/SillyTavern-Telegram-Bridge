"""Opt-in bounded final-request metrics, never a prompt optimizer or raw capture.

Only native builder scope can enable prefix comparison. Other helper calls still
contribute request/usage counts. All diagnostics are metadata; model/route/owner
identities are keyed for this runtime instance rather than exported as text.
"""

from __future__ import annotations

import copy
import math
import secrets
import threading
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from bridge.diagnostic_events import diagnostic_context, event
from bridge.memory_contracts import MemoryPromptContext, MemoryReadScope
from bridge.prompt_prefix_profile import PrefixProfiler
from bridge.prompt_profile_snapshot import MAX_REQUEST_BYTES, fingerprint
from bridge.request_observation_context import bind_observation
from bridge.token_usage_values import TokenUsage, UsageScope

PURPOSES = frozenset(
    {
        "story",
        "generation",
        "edit",
        "image",
        "epilogue",
        "summary",
        "scene",
        "memory",
        "npc",
        "choices",
        "director",
        "director_reconcile",
        "director_epilogue",
        "director_ending",
        "adjudication",
        "rank",
        "optimizer",
        "image_prompt",
        "render",
        "language",
        "humanizer",
        "unscoped",
    }
)
TRANSPORTS = frozenset({"chat_completions", "anthropic_messages", "openai_codex", "opencode_muse"})
PHASES = frozenset({"initial", "continuation", "recovery", "auth_retry"})
MAX_ACTIVE = 32


def _emit(fields: dict) -> None:
    event("provider.request_observed", **fields)


def usage_summary(records: list[dict]) -> dict:
    inputs = [r.get("input_tokens") for r in records]
    cached = [r.get("cached_tokens") for r in records]
    outputs = [r.get("output_tokens") for r in records]

    def known(values: list) -> int | None:
        counts = [v for v in values if type(v) is int]
        return sum(counts) if counts else None

    return {
        "requests": len(records),
        "known_input_tokens": known(inputs),
        "known_cached_tokens": known(cached),
        "known_output_tokens": known(outputs),
        "unknown_input_requests": sum(v is None for v in inputs),
        "unknown_cached_requests": sum(v is None for v in cached),
        "complete_usage_requests": sum(r.get("usage_complete") is True for r in records),
        "complete_input_tokens": known(inputs)
        if records and all(r.get("usage_complete") is True for r in records)
        else None,
        "accounting": "physical_generation_requests_not_accepted_work",
    }


def _shape(messages: list[dict], request_bytes: int) -> dict:
    """Count transport-visible text by role, never infer semantic section types."""
    missing = {"section_attribution": "unavailable", "full_prompt_token_count_known": False}
    if request_bytes > MAX_REQUEST_BYTES or len(messages) > 512:
        return missing
    counts = dict.fromkeys(("instruction_characters", "user_characters", "assistant_characters", "tool_characters"), 0)
    non_text = False
    roles = {
        "system": "instruction_characters",
        "developer": "instruction_characters",
        "user": "user_characters",
        "assistant": "assistant_characters",
        "tool": "tool_characters",
        "function": "tool_characters",
    }
    for message in messages:
        role = roles.get(str(message.get("role") or ""))
        content = message.get("content")
        if isinstance(content, str):
            size = len(content)
        elif isinstance(content, list) and len(content) <= 512:
            size = 0
            for part in content:
                if isinstance(part, dict) and part.get("type") in {"text", "input_text", "output_text"}:
                    text = part.get("text")
                    if not isinstance(text, str):
                        return missing
                    size += len(text)
                else:
                    non_text = True
        elif content is None:
            size = 0
            non_text = True
        else:
            return missing
        if role is None:
            return missing
        counts[role] += size
        non_text |= bool(message.get("tool_calls") or message.get("function_call"))
    return {
        **counts,
        "section_attribution": "wire_roles_only",
        "non_text_payload_present": non_text,
        "full_prompt_token_count_known": False,
    }


def _projection(messages: list[dict]) -> list[dict]:
    """Interpret native Responses text-block tags without changing the wire body."""
    result = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            content = [
                {**part, "type": "text", "observed_source_type": part["type"]}
                if isinstance(part, dict) and part.get("type") in {"input_text", "output_text"}
                else part
                for part in content
            ]
        result.append({**message, "content": content})
    return result


@dataclass(repr=False)
class _Ticket:
    owner: RequestObservationRuntime
    record: dict
    done: bool = False

    def usage(self, reading: object) -> None:
        with self.owner._lock:
            if self.done:
                return
            self.record["usage_readings"] += 1
            # More than one independent capture in one send is ambiguous, not additive.
            if not isinstance(reading, TokenUsage) or self.record["usage_readings"] != 1:
                self.record.update(input_tokens=None, cached_tokens=None, output_tokens=None, usage_complete=False)
                return
            values = {}
            for key in ("input_tokens", "cached_tokens", "output_tokens"):
                value = getattr(reading, key)
                values[key] = value if type(value) is int and 0 <= value <= 1_000_000_000 else None
            if values["cached_tokens"] is not None and (
                values["input_tokens"] is None or values["cached_tokens"] > values["input_tokens"]
            ):
                values["cached_tokens"] = None
            self.record.update(values)
            self.record["usage_complete"] = (
                reading.final is True and values["input_tokens"] is not None and values["output_tokens"] is not None
            )

    def finish(self, *, failed: bool, elapsed_ms: int) -> None:
        with self.owner._lock:
            if self.done:
                return
            self.done = True
            self.record.update(status="failed" if failed else "completed", elapsed_ms=elapsed_ms)
            self.record["usage_complete"] = self.record["usage_complete"] and not failed
            self.owner._active -= 1
            self.owner._finished += 1
            self.owner._records.append(dict(self.record))
        try:
            self.owner.emit(dict(self.record))
        except Exception:
            with self.owner._lock:
                self.owner._errors += 1


@dataclass(repr=False)
class RequestObservationRuntime:
    window: int = 8
    record_limit: int = 128
    emit: Callable[[dict], None] = _emit
    _key: bytes = field(default_factory=lambda: secrets.token_bytes(32), init=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False)
    _started: int = field(default=0, init=False)
    _finished: int = field(default=0, init=False)
    _active: int = field(default=0, init=False)
    _errors: int = field(default=0, init=False)
    _groups: dict[str, int] = field(default_factory=dict, init=False)
    _profiler: PrefixProfiler = field(init=False)
    _records: deque = field(init=False)

    def __post_init__(self) -> None:
        if type(self.record_limit) is not int or not 8 <= self.record_limit <= 256:
            raise ValueError("observation_record_limit")
        self._profiler = PrefixProfiler(window=self.window)
        self._records = deque(maxlen=self.record_limit)

    def bind(self, scope: object, identity: dict[str, str] | None) -> Callable[[], None]:
        return bind_observation(self, scope, identity)

    def profile_identity(self, messages: list[dict], session: dict) -> dict[str, str] | None:
        contexts = [m.get("_context_selection") for m in messages if "_context_selection" in m]
        if len(contexts) != 1 or not isinstance(contexts[0], MemoryPromptContext):
            return None
        scope = contexts[0].scope
        if (
            not isinstance(scope, MemoryReadScope)
            or scope.session_id != session.get("session_id")
            or not scope.chat_id
            or not scope.principals
            or not math.isfinite(scope.session_created_at)
            or scope.session_created_at <= 0
        ):
            return None
        native = [scope.chat_id, scope.session_id]
        character = {
            k: session.get(k, "")
            for k in ("character_file", "persona_id", "world_file", "system_prompt", "author_note", "response_language")
        }
        return {
            "owner": fingerprint(self._key, "owner", native),
            "session": fingerprint(self._key, "incarnation", [*native, scope.session_created_at]),
            "character": fingerprint(self._key, "character_configuration", character),
            "reader": fingerprint(self._key, "reader", [scope.principals, scope.consumer]),
            "branch": fingerprint(
                self._key,
                "branch",
                [
                    scope.rewrite_revision,
                    scope.external_epoch,
                    scope.rewrite_event_cutoff,
                    scope.explicit_event_cutoff,
                    scope.historical,
                    scope.through_rowid if scope.historical else None,
                ],
            ),
        }

    def begin(
        self,
        body: dict,
        messages: list[dict],
        *,
        scope: object,
        identity: dict[str, str] | None,
        selection: str,
        route: str,
        transport: str,
        phase: str,
        request_bytes: int,
    ) -> _Ticket | None:
        with self._lock:
            if self._active >= MAX_ACTIVE:
                self._errors += 1
                return None
            purpose = scope.purpose if isinstance(scope, UsageScope) and scope.purpose in PURPOSES else "unscoped"
            diagnostic = diagnostic_context()
            self._started += 1
            record = {
                "profile_request_id": "wire-" + secrets.token_hex(12),
                "observation_ordinal": self._started,
                "model_ref": fingerprint(self._key, "model", selection),
                "provider_ref": fingerprint(self._key, "route", [route, selection.partition("::")[0]]),
                "purpose": purpose,
                "transport": transport if transport in TRANSPORTS else "other",
                "phase": phase if phase in PHASES else "other",
                "attempt": diagnostic.get("attempt", 1),
                "bytes": request_bytes,
                "message_count": len(messages),
                "prefix_scope": "unavailable",
                "stable_prefix_characters": 0,
                "profile_sample_count": 0,
                "prefix_observed": False,
                "input_tokens": None,
                "cached_tokens": None,
                "output_tokens": None,
                "usage_readings": 0,
                "usage_complete": False,
            }
            record.update(_shape(messages, request_bytes))
            for key in ("call_id", "request_id"):
                if key in diagnostic:
                    record[key] = diagnostic[key]
            owner = (
                fingerprint(self._key, "owner", [scope.chat_id, scope.session_id])
                if isinstance(scope, UsageScope)
                else ""
            )
            if identity and identity.get("owner") == owner and request_bytes <= MAX_REQUEST_BYTES:
                try:
                    self._profile(body, messages, identity, record)
                except Exception:
                    self._errors += 1
                    record["prefix_scope"] = "profile_unavailable"
            self._active += 1
            return _Ticket(self, record)

    def _profile(self, body: dict, messages: list[dict], identity: dict, record: dict) -> None:
        scoped = {key: identity[key] for key in ("session", "character", "reader", "branch")}
        scoped["branch"] = fingerprint(
            self._key, "purpose_branch", [scoped["branch"], record["purpose"], record["phase"]]
        )
        scoped.update(provider=record["provider_ref"], model=record["model_ref"], stage="provider")
        group = fingerprint(self._key, "profile_group", scoped)
        envelope = {k: v for k, v in body.items() if k not in {"messages", "input", "system", "instructions"}}
        view = {**envelope, "messages": _projection(messages), "observed_transport": record["transport"]}
        self._profiler.observe(view, scoped)
        self._groups.setdefault(group, len(self._groups))
        summary = self._profiler.report()["cohorts"][self._groups[group]]
        record.update(
            prefix_scope="native_prompt",
            stable_prefix_characters=summary["stable_prefix_characters"],
            profile_sample_count=summary["retained_samples"],
            prefix_observed=summary["retained_samples"] >= 2,
            instruction_duplicate_groups=len(summary["latest_instruction_duplicates"]),
            full_prompt_token_count_known=False,
        )

    def report(self) -> dict:
        with self._lock:
            records = [dict(r) for r in self._records]
            return {
                "schema_version": 1,
                "type": "opt_in_final_request_observation",
                "requests_started": self._started,
                "requests_finished": self._finished,
                "active_requests": self._active,
                "observer_errors": self._errors,
                "record_limit": self.record_limit,
                "evicted_records": self._finished - len(records),
                "records": records,
                "window_usage": usage_summary(records),
                "prefix_profile": copy.deepcopy(self._profiler.report()),
                "optimization_authorized": False,
                "accepted_work_savings_fraction": None,
                "limitations": [
                    "Requests are observed before transport open; remote receipt is not proven.",
                    "Prefix analysis uses normalized transport text, not a provider cache-hit prediction.",
                    "Native prompt identity describes the assembled reader scope; it grants no new source access.",
                    "Unknown usage and dropped observations prevent complete cost claims.",
                ],
            }


def build_request_observer(environ: Mapping[str, str]) -> RequestObservationRuntime | None:
    enabled = environ.get("SILLYTAVERN_REQUEST_OBSERVATION", "off").strip().casefold()
    if enabled in {"off", "false", "0", "no", ""}:
        return None
    if enabled not in {"on", "true", "1", "yes"}:
        raise ValueError("SILLYTAVERN_REQUEST_OBSERVATION must be on or off")
    return RequestObservationRuntime()
