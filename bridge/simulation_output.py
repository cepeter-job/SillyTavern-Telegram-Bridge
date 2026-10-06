"""Reserved output suppression and narrow legacy-template filtering of effective copies."""

from __future__ import annotations

import re

_TAG = re.compile(r"<\s*(/?)\s*internal_states(?=[\s/>]|$)[^<>]*(?:>|(?=<)|$)", re.I)
_ENCODED_LEFT = re.compile(r"&(?:amp;)*(?:lt;|#0*60;|#x0*3c;)", re.I)
_NAME = re.compile(r"\s*/?\s*([a-z_]*)", re.I)
_ENCODED_END = re.compile(r"(<\s*/?\s*internal_states(?=[\s/&>]|$)[^<>]*?)&(?:amp;)*(?:gt;|#0*62;|#x0*3e;)", re.I)
_HEADING = re.compile(r"^(#{1,6})[ \t]+([^\n]+)$", re.M)


def _reserved_angles(source: str) -> str:
    """Decode only reserved angle delimiters, including chained amp escaping, in one pass."""
    pieces: list[str] = []
    cursor = 0
    for match in _ENCODED_LEFT.finditer(source):
        name = _NAME.match(source, match.end())
        if name is None:
            continue
        tag_name = name.group(1).casefold()
        complete = tag_name == "internal_states" and (
            name.end() == len(source) or source[name.end()].isspace() or source[name.end()] in "/&>"
        )
        partial = name.end() == len(source) and "internal_states".startswith(tag_name)
        if complete or partial:
            pieces.extend((source[cursor : match.start()], "<"))
            cursor = match.end()
    pieces.append(source[cursor:])
    return _ENCODED_END.sub(r"\1>", "".join(pieces))


def strip_internal_state_blocks(text: str, *, streaming: bool = False) -> str:
    source = _reserved_angles(str(text or ""))
    pieces = []
    cursor = depth = 0
    for match in _TAG.finditer(source):
        if depth == 0:
            pieces.append(source[cursor : match.start()])
        if match.group(1):
            depth = max(0, depth - 1)
        elif not match.group().rstrip().endswith("/>"):
            depth += 1
        cursor = match.end()
    if depth == 0:
        tail = source[cursor:]
        position = tail.rfind("<")
        if position >= 0:
            candidate = re.sub(r"\s+", "", tail[position:]).casefold()
            reserved = "<internal_states"
            partial = reserved.startswith(candidate) and (streaming or len(candidate) >= len("<internal_"))
            unclosed = candidate.startswith(reserved) and (
                len(candidate) == len(reserved)
                or candidate[len(reserved) : len(reserved) + 1] in {"/", ">"}
                or re.match(r"<\s*internal_states\s", tail[position:], re.I)
            )
            if partial or (unclosed and ">" not in tail[position:]):
                tail = tail[:position]
        pieces.append(tail)
    return "".join(pieces)


def strip_legacy_tracker_templates(text: str) -> str:
    source = str(text or "")
    headings = list(_HEADING.finditer(source))
    remove: list[tuple[int, int]] = []
    for index, heading in enumerate(headings):
        title = heading.group(2).strip().casefold().strip(":")
        if title not in {"internal states", "internal state trackers", "internal states tracker"}:
            continue
        end = next(
            (
                next_heading.start()
                for next_heading in headings[index + 1 :]
                if len(next_heading.group(1)) <= len(heading.group(1))
            ),
            len(source),
        )
        body = source[heading.end() : end]
        if all(re.search(rf"\b{word}\b", body, re.I) for word in ("bond", "sparks", "grudge")) and re.search(
            r"\b(output|print|append|display|template|every turn)\b", body, re.I
        ):
            if not remove or heading.start() >= remove[-1][1]:
                remove.append((heading.start(), end))
    for start, end in reversed(remove):
        source = source[:start] + source[end:]
    return strip_internal_state_blocks(source)


def effective_tracker_prompt(session: dict, fields: dict) -> tuple[dict, dict]:
    effective_session = dict(session)
    for name in ("system_prompt", "author_note"):
        effective_session[name] = strip_legacy_tracker_templates(session.get(name, ""))
    effective_fields = {
        name: strip_legacy_tracker_templates(value) if isinstance(value, str) else value
        for name, value in fields.items()
    }
    return effective_session, effective_fields


def visible_story_history(rows: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [(role, strip_internal_state_blocks(text) if role == "assistant" else text) for role, text in rows]
