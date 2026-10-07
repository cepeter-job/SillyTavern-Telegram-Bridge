"""Prompt-only retirement of legacy assistant tracker ledgers.

Stored transcript text is intentionally immutable. This module only removes the
retired Internal States ledger when old assistant text is reused as model
context or derived-memory source material.
"""

from __future__ import annotations

import re

_MARKER = re.compile(
    r"(?im)^[ \t]*(?:#{1,6}[ \t]*)?(?:🎽[ \t]*)?INTERNAL STATES"
    r"(?:[ \t]*\([^\n]*\))?[ \t]*$"
)
_LEGACY_SECTION = re.compile(
    r"(?im)^[ \t]*(?:👤[ \t]*)?(?:NPC AGENDAS|NPC LOCATIONS)|"
    r"^[ \t]*(?:💚[ \t]*)?BONDS|"
    r"^[ \t]*(?:📜[ \t]*)?QUESTS|"
    r"^[ \t]*(?:🎒[ \t]*)?INV(?:ENTORY)?(?:[ \t]*&[ \t]*SKILLS)?|"
    r"^[ \t]*(?:🧠[ \t]*)?INTERNAL THOUGHTS|"
    r"^[ \t]*(?:📓[ \t]*)?GM(?:'S)? NOTEBOOK|"
    r"^[ \t]*(?:🎲[ \t]*)?DND TASK SIM|"
    r"^[ \t]*(?:🌌[ \t]*)?PHYSICS"
)
_TG_SPOILER = re.compile(r"(?is)<tg-spoiler\b[^>]*>.*?</tg-spoiler\s*>")
_DETAILS = re.compile(r"(?is)<details\b[^>]*>.*?</details\s*>")


def _is_legacy_block(value: str) -> bool:
    return bool(_MARKER.search(value) and _LEGACY_SECTION.search(value))


def strip_legacy_tracker_blocks(value: str) -> str:
    """Remove only recognizable legacy tracker ledgers from prompt-bound text."""

    if not value or "internal states" not in value.casefold():
        return value

    def remove_wrapped(match: re.Match[str]) -> str:
        return "" if _is_legacy_block(match.group(0)) else match.group(0)

    cleaned = _TG_SPOILER.sub(remove_wrapped, value)
    cleaned = _DETAILS.sub(remove_wrapped, cleaned)

    marker = _MARKER.search(cleaned)
    if marker and _LEGACY_SECTION.search(cleaned[marker.end() :]):
        # Historical prompt protocol always appended the ledger after story prose.
        # If a wrapper was left unterminated, remove its opening tag as well.
        start = marker.start()
        prefix = cleaned[:start]
        open_tag = re.search(r"(?is)<tg-spoiler\b[^>]*>[ \t\r\n]*$", prefix)
        if open_tag:
            start = open_tag.start()
        cleaned = cleaned[:start]

    return cleaned.rstrip()


def prompt_text(role: str, value: str) -> str:
    """Return transcript text suitable for model context without mutating storage."""

    return strip_legacy_tracker_blocks(value) if str(role).casefold() == "assistant" else value
