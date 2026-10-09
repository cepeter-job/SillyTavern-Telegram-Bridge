"""Bounded, keyed, content-free observations of explicitly supplied prompts.

No prompt is edited and no raw content or identity survives in a snapshot. Native
optional spans come from construction metadata, never from parsing story text.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass

CHUNK_CHARACTERS = 128
MAX_MESSAGES = 512
MAX_SECTIONS = 1024
MAX_REQUEST_BYTES = 2_000_000
MAX_TEXT_CHARACTERS = 500_000
SCOPE_FIELDS = frozenset({"session", "character", "reader", "branch", "provider", "model", "stage"})
STAGES = frozenset({"builder", "provider", "partial_replay"})
ROLES = frozenset({"system", "developer", "user", "assistant", "tool", "function"})
INSTRUCTIONS = frozenset({"system", "developer"})
OPTIONAL_KINDS = frozenset({"memory", "episodic", "npc", "simulation", "summary", "rag"})
_INTERNAL = frozenset(
    {
        "_context_optional",
        "_context_selection",
        "_context_history_index",
        "_context_section_chars",
    }
)


def encoded(value: object) -> bytes:
    """Canonical JSON identity, not a claim about a provider tokenizer/serialization."""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise ValueError("profile_invalid_json") from None


def fingerprint(key: bytes, domain: str, value: object) -> str:
    return hmac.new(key, domain.encode() + b"\0" + encoded(value), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class MessageStamp:
    role: str
    digest: str
    layout: str
    characters: int
    chunks: tuple[str, ...]
    plain_text: bool
    non_text: bool


@dataclass(frozen=True)
class SectionStamp:
    kind: str
    ordinal: int
    digest: str
    characters: int


@dataclass(frozen=True)
class PromptSnapshot:
    scope: str
    stage: str
    envelope: str
    messages: tuple[MessageStamp, ...]
    sections: tuple[SectionStamp, ...]
    usage: tuple[int | None, int | None, int | None]
    text_characters: int
    non_text: bool


def _scope(scope: dict, key: bytes) -> tuple[str, str]:
    if not isinstance(scope, dict) or set(scope) != SCOPE_FIELDS:
        raise ValueError("profile_invalid_scope")
    if any(type(value) is not str or not value.strip() or len(value) > 1024 for value in scope.values()):
        raise ValueError("profile_invalid_scope")
    if scope["stage"] not in STAGES:
        raise ValueError("profile_invalid_scope")
    return fingerprint(key, "scope", scope), scope["stage"]


def _usage(usage: dict | None) -> tuple[int | None, int | None, int | None]:
    if usage is None:
        return None, None, None
    if not isinstance(usage, dict):
        raise ValueError("profile_invalid_usage")
    values = tuple(usage.get(name) for name in ("input_tokens", "cached_tokens", "output_tokens"))
    if any(value is not None and (type(value) is not int or not 0 <= value <= 2**31 - 1) for value in values):
        raise ValueError("profile_invalid_usage")
    inputs, cached, output = values
    if cached is not None and (inputs is None or cached > inputs):
        raise ValueError("profile_invalid_usage")
    return inputs, cached, output


def _text(content: object) -> tuple[str, bool]:
    if content is None:
        return "", True
    if isinstance(content, str):
        return content, False
    if not isinstance(content, list) or len(content) > MAX_MESSAGES:
        raise ValueError("profile_invalid_content")
    parts = []
    non_text = False
    for part in content:
        if not isinstance(part, dict):
            raise ValueError("profile_invalid_content")
        if part.get("type") == "text":
            if not isinstance(part.get("text"), str):
                raise ValueError("profile_invalid_content")
            parts.append(part["text"])
        else:
            non_text = True
    return "".join(parts), non_text


def _sections(message: dict, text: str) -> list[tuple[str, str]]:
    spans = message.get("_context_optional", [])
    if not isinstance(spans, (list, tuple)) or len(spans) > MAX_MESSAGES:
        raise ValueError("profile_invalid_sections")
    intervals = []
    for span in spans:
        if not isinstance(span, dict) or not {"kind", "start", "end"}.issubset(span):
            raise ValueError("profile_invalid_sections")
        kind, start, end = span["kind"], span["start"], span["end"]
        if (
            not isinstance(kind, str)
            or kind not in OPTIONAL_KINDS
            or type(start) is not int
            or type(end) is not int
            or not 0 <= start <= end <= len(text)
        ):
            raise ValueError("profile_invalid_sections")
        intervals.append((start, end, kind))
    # Native spans in a multimodal message address its one text part, not a
    # concatenation of unrelated blocks; ambiguity must not invent provenance.
    if intervals and isinstance(message.get("content"), list):
        text_parts = [p for p in message["content"] if isinstance(p, dict) and p.get("type") == "text"]
        if len(text_parts) != 1:
            raise ValueError("profile_invalid_sections")
    remainder = "instruction_remainder" if message["role"] in INSTRUCTIONS else "dialogue_or_task"
    result = []
    cursor = 0
    for start, end, kind in sorted(intervals):
        if start < cursor:
            raise ValueError("profile_invalid_sections")
        if start > cursor:
            result.append((remainder, text[cursor:start]))
        if end > start:
            result.append((kind, text[start:end]))
        cursor = end
    if cursor < len(text):
        result.append((remainder, text[cursor:]))
    return result


def snapshot(request: dict, scope: dict, usage: dict | None, key: bytes) -> PromptSnapshot:
    """Read one request into hashes/counts, validating before retained state changes."""
    scope_hash, stage = _scope(scope, key)
    observed_usage = _usage(usage)
    if not isinstance(request, dict) or not isinstance(request.get("messages"), list):
        raise ValueError("profile_invalid_request")
    messages = request["messages"]
    if not 1 <= len(messages) <= MAX_MESSAGES:
        raise ValueError("profile_message_limit")
    wire_messages = []
    total_chars = 0
    for message in messages:
        if not isinstance(message, dict) or not isinstance(message.get("role"), str) or message["role"] not in ROLES:
            raise ValueError("profile_invalid_role")
        text, _ = _text(message.get("content"))
        total_chars += len(text)
        if total_chars > MAX_TEXT_CHARACTERS:
            raise ValueError("profile_text_limit")
        wire_messages.append({k: v for k, v in message.items() if k not in _INTERNAL})
    envelope = {k: v for k, v in request.items() if k != "messages"}
    if len(encoded({**envelope, "messages": wire_messages})) > MAX_REQUEST_BYTES:
        raise ValueError("profile_request_limit")
    stamps = []
    sections = []
    ordinals: dict[str, int] = {}
    for original, message in zip(messages, wire_messages, strict=True):
        text, non_text = _text(message.get("content"))
        layout = fingerprint(key, "layout", {k: v for k, v in message.items() if k != "content"})
        chunks = ()
        if message["role"] in INSTRUCTIONS and isinstance(message.get("content"), str):
            # Only full blocks participate in a partial-prefix lower bound.
            chunks = tuple(
                fingerprint(key, "prefix_chunk", text[i : i + CHUNK_CHARACTERS])
                for i in range(0, len(text) - CHUNK_CHARACTERS + 1, CHUNK_CHARACTERS)
            )
        stamps.append(
            MessageStamp(
                message["role"],
                fingerprint(key, "message", message),
                layout,
                len(text),
                chunks,
                isinstance(message.get("content"), str),
                non_text or bool(message.get("tool_calls") or message.get("function_call")),
            )
        )
        for kind, value in _sections(original, text):
            if len(sections) >= MAX_SECTIONS:
                raise ValueError("profile_section_limit")
            ordinal = ordinals.get(kind, 0)
            ordinals[kind] = ordinal + 1
            sections.append(SectionStamp(kind, ordinal, fingerprint(key, "section", [layout, kind, value]), len(value)))
    return PromptSnapshot(
        scope_hash,
        stage,
        fingerprint(key, "envelope", envelope),
        tuple(stamps),
        tuple(sections),
        observed_usage,
        total_chars,
        any(m.non_text for m in stamps) or bool(envelope.get("tools")),
    )
