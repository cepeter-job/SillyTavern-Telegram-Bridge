"""Pure text-unit and escape calculations shared by Telegram formatting paths."""

from __future__ import annotations


def utf16_length(value: str) -> int:
    """Count Telegram entity code units, not Unicode code points or UTF-8 bytes."""
    return len(str(value).encode("utf-16-le")) // 2


def is_escaped_at(text: str, index: int) -> bool:
    """Whether the character at index follows an odd run of backslashes."""
    backslashes = 0
    cursor = index - 1
    while cursor >= 0 and text[cursor] == "\\":
        backslashes += 1
        cursor -= 1
    return bool(backslashes % 2)
