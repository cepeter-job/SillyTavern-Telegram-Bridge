"""Deterministic roleplay transport formatting for Telegram delivery."""

from __future__ import annotations

import re

_CODE = re.compile(r"```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`")
_HTML_CODE = re.compile(
    r"<(?P<tag>code|pre)\b[^>]*>[\s\S]*?</(?P=tag)\s*>",
    re.IGNORECASE,
)
_HTML_TAG = re.compile(r"</?[A-Za-z][^>\n]*>")
_CODE_TOKEN = re.compile(r"\x00RPCODE(?P<index>\d+)\x00")
_HTML_TOKEN = re.compile(r"\x00RPHTML(?P<index>\d+)\x00")
_SINGLE_STAR = re.compile(r"(?<!\*)\*(?!\*)")
_ROLEPLAY_ITALIC = re.compile(r"(?<!\*)\*(?![\s*])(?P<body>.*?)(?<![\s*])\*(?!\*)", re.DOTALL)
_QUOTE_PAIRS = {'"': '"', "“": "”", "«": "»"}
_ESCAPED_PARAGRAPH = re.compile(r"(?<!\\)\\n(?<!\\)\\n")
_ESCAPED_LINE = re.compile(r"(?<=[.!?*\"'])\\n")
_ESCAPED_QUOTE = re.compile(r'(?<!\\)\\"')


def _is_escaped(text: str, index: int) -> bool:
    backslashes = 0
    cursor = index - 1
    while cursor >= 0 and text[cursor] == "\\":
        backslashes += 1
        cursor -= 1
    return bool(backslashes % 2)


def _quoted_speech_ranges(text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    opened_at: int | None = None
    closing = ""
    for index, char in enumerate(text):
        if opened_at is None:
            if char in _QUOTE_PAIRS and (char != '"' or not _is_escaped(text, index)):
                opened_at = index
                closing = _QUOTE_PAIRS[char]
            continue
        if char == closing and (char != '"' or not _is_escaped(text, index)):
            ranges.append((opened_at, index + 1))
            opened_at = None
            closing = ""
    if opened_at is not None:
        ranges.append((opened_at, len(text)))
    return ranges


def _wrap_narration(segment: str) -> str:
    if not segment or not segment.strip():
        return segment
    leading_len = len(segment) - len(segment.lstrip())
    trailing_len = len(segment) - len(segment.rstrip())
    body_end = len(segment) - trailing_len if trailing_len else len(segment)
    leading = segment[:leading_len]
    body = segment[leading_len:body_end]
    trailing = segment[body_end:]
    if not body:
        return segment
    return f"{leading}*{body}*{trailing}"


def normalize_roleplay_transport(
    text: str,
    *,
    preserve_authored_unquoted_dialogue: bool = False,
) -> str:
    """Normalize roleplay transport while optionally preserving authored ST syntax."""
    protected_code: list[str] = []
    protected_html: list[str] = []

    def protect_code(match: re.Match[str]) -> str:
        token = f"\x00RPCODE{len(protected_code)}\x00"
        protected_code.append(match.group(0))
        return token

    def protect_html(match: re.Match[str]) -> str:
        token = f"\x00RPHTML{len(protected_html)}\x00"
        protected_html.append(match.group(0))
        return token

    def restore_protected(value: str) -> str:
        for index, html in enumerate(protected_html):
            value = value.replace(f"\x00RPHTML{index}\x00", html)
        for index, code in enumerate(protected_code):
            value = value.replace(f"\x00RPCODE{index}\x00", code)
        return value

    source = _CODE.sub(protect_code, str(text or ""))
    source = _HTML_CODE.sub(protect_code, source)
    source = _HTML_TAG.sub(protect_html, source)
    # Some providers return JSON-escaped prose rather than actual line breaks.
    # Decode only paragraph markers or markers after sentence punctuation: a
    # blanket unescape would corrupt literal paths such as C:\\new and code.
    repaired = _ESCAPED_PARAGRAPH.sub("\n\n", source)
    repaired = _ESCAPED_LINE.sub("\n", repaired)
    if repaired != source:
        source = _ESCAPED_QUOTE.sub('"', repaired)
    single_stars = list(_SINGLE_STAR.finditer(source))
    valid_spans = list(_ROLEPLAY_ITALIC.finditer(source))
    if valid_spans and len(single_stars) == 2 * len(valid_spans):
        return restore_protected(source)

    if preserve_authored_unquoted_dialogue:
        stripped = _SINGLE_STAR.sub("", source)
        if not _quoted_speech_ranges(stripped):
            return restore_protected(source)

    source = _SINGLE_STAR.sub("", source)
    if not source.strip():
        return restore_protected(source)

    protected_ranges = _quoted_speech_ranges(source)
    protected_ranges.extend((match.start(), match.end()) for match in _CODE_TOKEN.finditer(source))
    protected_ranges.extend((match.start(), match.end()) for match in _HTML_TOKEN.finditer(source))
    protected_ranges.sort()

    parts: list[str] = []
    cursor = 0
    for start, end in protected_ranges:
        if end <= cursor:
            continue
        if start > cursor:
            parts.append(_wrap_narration(source[cursor:start]))
        parts.append(source[max(start, cursor) : end])
        cursor = end
    if cursor < len(source):
        parts.append(_wrap_narration(source[cursor:]))

    return restore_protected("".join(parts))
