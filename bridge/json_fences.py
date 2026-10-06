"""Shared JSON fence handling for the episodic-memory and NPC extractors.

Protocol-specific length limits and shape validation remain with each caller.
The bounded Light Novel parser intentionally owns its stricter fence contract.
"""

from __future__ import annotations


def unfence_json(value: str | None) -> str:
    """Remove the optional complete fence accepted by the existing extractors."""
    text = str(value or "").strip()
    fence = chr(96) * 3
    if not (text.startswith(fence) and text.endswith(fence)):
        return text
    inner = text[len(fence) : -len(fence)].strip()
    if inner.casefold().startswith("json"):
        inner = inner[4:].lstrip()
    return inner
