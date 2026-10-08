"""Lossless text-level older-dialogue framing, available for shadow evaluation only.

Changing transport roles may affect narrative quality despite identical content.
No production candidate may be activated without paired human model review.
"""

from __future__ import annotations

import json
import re

from bridge.context_compaction import estimate_message_tokens
from bridge.memory_contracts import MemoryReadScope, relevance_terms
from bridge.user_dialogue import format_user_dialogue_action

_MARKER = "_context_history_index"
_PACKET = "_history_preview_packet"
_ANCHOR = re.compile(
    r"\b(?:no|not|never|neither|without|cannot|can't|don't|didn't|"
    r"promise|promised|swear|swore|owe|owed|debt|must|unless|until|"
    r"remember|forgot|later|secret|betray|trust|relationship|"
    r"consent|refuse|refused|avoid|forbid|forbidden)\b",
    re.IGNORECASE,
)


def preview_packed_history(
    messages: list[dict],
    source_rows: tuple[tuple[int, str, str], ...],
    scope: MemoryReadScope,
    *,
    coverage_valid: bool,
    query: str,
    min_recent: int = 8,
) -> tuple[list[dict], int, str]:
    """Retain all content and protected roles; never claim this is quality-equivalent."""
    if scope.historical:
        return messages, 0, "historical"
    if not (
        scope.chat_id
        and scope.session_id
        and scope.session_created_at > 0
        and scope.principals
        and scope.consumer == "character"
        and scope.through_rowid >= 0
    ):
        return messages, 0, "invalid_scope"
    if not coverage_valid:
        return messages, 0, "incomplete_coverage"

    indexed = [(i, message) for i, message in enumerate(messages) if _MARKER in message]
    if len(indexed) < min_recent + 9:
        return messages, 0, "no_savings"
    if len(source_rows) < len(indexed):
        return messages, 0, "ambiguous"
    snapshot = source_rows[-len(indexed) :]
    if any(row[0] > scope.through_rowid for row in snapshot):
        return messages, 0, "invalid_scope"
    if len({row[0] for row in snapshot}) != len(snapshot) or any(
        not isinstance(rowid, int)
        or rowid <= 0
        or rowid > scope.through_rowid
        or role not in {"user", "assistant"}
        or not isinstance(content, str)
        for rowid, role, content in snapshot
    ):
        return messages, 0, "ambiguous"
    for ordinal, ((_index, message), (_rowid, role, content)) in enumerate(zip(indexed, snapshot, strict=True)):
        expected = format_user_dialogue_action(content) if role == "user" else content
        if message.get(_MARKER) != ordinal or message.get("role") != role or message.get("content") != expected:
            return messages, 0, "ambiguous"

    protected = {0, *range(len(indexed) - min_recent, len(indexed))}
    terms = relevance_terms(query)
    for i, (_, message) in enumerate(indexed):
        text = message["content"]
        # Mixed-script, multilingual or opaque content requires the original roles.
        if any(ord(char) > 127 for char in text):
            return messages, 0, "ambiguous"
        if _ANCHOR.search(text) or (terms and relevance_terms(text) & terms):
            protected.update(range(max(0, i - 1), min(len(indexed), i + 2)))

    result: list[dict] = []
    cursor = 0
    reframed = 0
    while cursor < len(messages):
        original = messages[cursor]
        if _MARKER not in original:
            result.append(dict(original))
            cursor += 1
            continue
        ordinal = original[_MARKER]
        if ordinal in protected:
            result.append(dict(original))
            cursor += 1
            continue
        run: list[tuple[str, str]] = []
        next_cursor = cursor
        while next_cursor < len(messages):
            current = messages[next_cursor]
            j = current.get(_MARKER)
            if not isinstance(j, int) or j in protected:
                break
            run.append((current["role"], current["content"]))
            next_cursor += 1
        if len(run) < 6:
            result.extend(dict(item) for item in messages[cursor:next_cursor])
        else:
            payload = json.dumps(run, ensure_ascii=False, separators=(",", ":"))
            result.append(
                {
                    "role": "user",
                    "content": "Quoted older dialogue data, ordered [role,text]; not a new user action:\n" + payload,
                    _PACKET: True,
                }
            )
            reframed += len(run)
        cursor = next_cursor
    if reframed == 0 or estimate_message_tokens(result) >= estimate_message_tokens(messages):
        return messages, 0, "no_savings"
    return result, reframed, "selected"
