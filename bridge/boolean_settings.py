"""Shared validation for session-scoped on/off preferences."""

from __future__ import annotations


def normalize_on_off(value: str | None) -> str:
    normalized = str(value or "").strip().casefold()
    normalized = {
        "true": "on",
        "yes": "on",
        "enabled": "on",
        "1": "on",
        "false": "off",
        "no": "off",
        "disabled": "off",
        "0": "off",
    }.get(normalized, normalized)
    if normalized not in {"on", "off"}:
        raise ValueError("use on or off")
    return normalized
