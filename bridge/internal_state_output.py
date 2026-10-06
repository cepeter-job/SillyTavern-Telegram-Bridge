"""Keep model-internal state in history while excluding it from user delivery."""

from __future__ import annotations

import re
from collections.abc import Callable

_COMPLETE_INTERNAL_STATE = re.compile(
    r"(?is)"
    r"(?:[ \t]*<!--\s*GFX_START\s*-->\s*)?"
    r"(?:<tg-spoiler\b[^>]*>\s*)?"
    r"(?P<state><internal_states\b[^>]*>.*?</internal_states\s*>)"
    r"(?:\s*</tg-spoiler\s*>)?"
    r"(?:\s*<!--\s*GFX_END\s*-->)?"
)
_OPEN_INTERNAL_STATE = re.compile(
    r"(?is)"
    r"(?:[ \t]*<!--\s*GFX_START\s*-->\s*)?"
    r"(?:<tg-spoiler\b[^>]*>\s*)?"
    r"(?P<state><internal_states\b[^>]*>)"
)


def split_internal_states(text: str) -> tuple[str, str]:
    """Return user-visible text and model-private internal-state markup.

    Complete blocks are removed wherever they occur and retained at the end of
    history. A trailing incomplete block is also treated as private so streaming
    previews cannot leak it while the provider is still producing the closing tag.
    """

    source = str(text or "")
    visible_parts: list[str] = []
    hidden_parts: list[str] = []
    cursor = 0

    for match in _COMPLETE_INTERNAL_STATE.finditer(source):
        visible_parts.append(source[cursor : match.start()])
        hidden_parts.append(match.group("state").strip())
        cursor = match.end()

    remainder = source[cursor:]
    trailing_open = _OPEN_INTERNAL_STATE.search(remainder)
    if trailing_open is None:
        visible_parts.append(remainder)
    else:
        visible_parts.append(remainder[: trailing_open.start()])
        hidden_parts.append(remainder[trailing_open.start("state") :].strip())

    visible = "".join(visible_parts).rstrip()
    hidden = "\n\n".join(part for part in hidden_parts if part).strip()
    return visible, hidden


def join_visible_and_internal_states(visible: str, hidden: str) -> str:
    visible = str(visible or "").rstrip()
    hidden = str(hidden or "").strip()
    if visible and hidden:
        return f"{visible}\n\n{hidden}"
    return visible or hidden


def map_visible_reply(text: str, transform: Callable[[str], str]) -> str:
    """Transform only the user-visible portion while preserving private state."""

    visible, hidden = split_internal_states(text)
    return join_visible_and_internal_states(transform(visible), hidden)


def delivery_visible_reply(text: str) -> str:
    """Return only content eligible for Telegram/expression/TTS delivery."""

    visible, _hidden = split_internal_states(text)
    return visible
