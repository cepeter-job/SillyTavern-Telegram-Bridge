"""Normalize completed model output for safe Telegram delivery."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser

_CODE = re.compile(r"```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`")
_AUTOLINK = re.compile(r"<(?:https?://[^>\s]+|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})>")
_BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "div",
    "dl",
    "dt",
    "dd",
    "fieldset",
    "figcaption",
    "figure",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
}
_SKIP_CONTENT = {"script", "style"}
_CANONICAL_TAGS = {
    "b": "b",
    "strong": "b",
    "i": "i",
    "em": "i",
    "u": "u",
    "ins": "u",
    "s": "s",
    "strike": "s",
    "del": "s",
    "tg-spoiler": "tg-spoiler",
    "code": "code",
    "pre": "pre",
}
_ENTITY_TYPES = {
    "b": "bold",
    "i": "italic",
    "u": "underline",
    "s": "strikethrough",
    "tg-spoiler": "spoiler",
    "code": "code",
    "pre": "pre",
}
_STYLE_ENTITY_TYPES = {"bold", "italic", "underline", "strikethrough", "spoiler"}
_HARD_EXCLUSIVE_ENTITY_TYPES = {"code", "pre"}


@dataclass(frozen=True)
class TelegramFormatSpan:
    """One allowlisted Telegram entity span using Python character offsets."""

    type: str
    start: int
    end: int
    url: str = ""


@dataclass
class _OpenEntity:
    tag: str
    type: str
    start: int
    url: str = ""


def _safe_href(value: str | None) -> str:
    href = str(value or "").strip()
    return href if href.startswith(("https://", "http://")) else ""


def _mask_literals(source: str, prefix: str) -> tuple[str, list[str]]:
    protected: list[str] = []

    def protect(match: re.Match[str]) -> str:
        token = f"\x00{prefix}{len(protected)}\x00"
        protected.append(match.group(0))
        return token

    masked = _CODE.sub(protect, source)
    return _AUTOLINK.sub(protect, masked), protected


def _restore_literals(value: str, protected: list[str], prefix: str) -> str:
    for index, literal in enumerate(protected):
        value = value.replace(f"\x00{prefix}{index}\x00", literal)
    return value


def _append_newline(parts: list[str]) -> None:
    if not parts or parts[-1].endswith(("\n", " ", "\t")):
        return
    parts.append("\n")


class _PlainTelegramHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0
        self.links: list[tuple[str, int]] = []

    def _newline(self) -> None:
        _append_newline(self.parts)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.casefold()
        if name in _SKIP_CONTENT:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if name == "a":
            href = next((str(value or "") for key, value in attrs if key.casefold() == "href"), "").strip()
            self.links.append((_safe_href(href), len(self.parts)))
        if name == "br":
            self._newline()
        elif name == "li":
            self._newline()
            self.parts.append("- ")
        elif name in _BLOCK_TAGS:
            self._newline()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        name = tag.casefold()
        if name in _SKIP_CONTENT:
            if self.skip_depth:
                self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if name == "a":
            href, start = self.links.pop() if self.links else ("", len(self.parts))
            visible = "".join(self.parts[start:]).strip()
            if href and href not in visible:
                self.parts.append(f" ({href})")
        if name in _BLOCK_TAGS or name == "li":
            self._newline()

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.parts.append(data)


class _TransportTelegramHTML(HTMLParser):
    """Strip unknown HTML while retaining Telegram-safe presentation tags."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0
        self.open_tags: list[str] = []

    def _newline(self) -> None:
        _append_newline(self.parts)

    def _open(self, canonical: str, rendered: str | None = None) -> None:
        if canonical in {"pre", "blockquote"}:
            self._newline()
        self.parts.append(rendered or f"<{canonical}>")
        self.open_tags.append(canonical)

    def _close(self, canonical: str) -> None:
        if canonical not in self.open_tags:
            return
        index = len(self.open_tags) - 1 - self.open_tags[::-1].index(canonical)
        closing = self.open_tags[index:]
        for tag in reversed(closing):
            self.parts.append(f"</{tag}>")
        del self.open_tags[index:]
        if canonical in {"pre", "blockquote"}:
            self._newline()

    def finish(self) -> None:
        for tag in reversed(self.open_tags):
            self.parts.append(f"</{tag}>")
        self.open_tags.clear()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.casefold()
        if name in _SKIP_CONTENT:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if name == "br":
            self._newline()
            return
        if name == "li":
            self._newline()
            self.parts.append("- ")
            return
        if name == "a":
            href = _safe_href(next((value for key, value in attrs if key.casefold() == "href"), ""))
            if href:
                self._open("a", f'<a href="{escape(href, quote=True)}">')
            return
        if name == "blockquote":
            expandable = any(key.casefold() == "expandable" for key, _ in attrs)
            self._open("blockquote", "<blockquote expandable>" if expandable else "<blockquote>")
            return
        canonical = _CANONICAL_TAGS.get(name)
        if canonical:
            self._open(canonical)
            return
        if name in _BLOCK_TAGS:
            self._newline()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.casefold()
        if name == "br":
            self._newline()
            return
        before = len(self.open_tags)
        self.handle_starttag(tag, attrs)
        if len(self.open_tags) > before:
            self._close(self.open_tags[-1])

    def handle_endtag(self, tag: str) -> None:
        name = tag.casefold()
        if name in _SKIP_CONTENT:
            if self.skip_depth:
                self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if name == "li":
            self._newline()
            return
        if name in {"a", "blockquote"}:
            self._close(name)
            return
        canonical = _CANONICAL_TAGS.get(name)
        if canonical:
            self._close(canonical)
            return
        if name in _BLOCK_TAGS:
            self._newline()

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        if self.parts and self.parts[-1].endswith("\n") and data.startswith("\n"):
            data = data[1:]
        if data:
            self.parts.append(data)


class _EntityTelegramHTML(HTMLParser):
    def __init__(self, protected: list[str], prefix: str) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.char_count = 0
        self.open_entities: list[_OpenEntity] = []
        self.spans: list[TelegramFormatSpan] = []
        self.protected = protected
        self.prefix = prefix

    def _append(self, data: str) -> None:
        expanded = _restore_literals(data, self.protected, self.prefix)
        self.parts.append(expanded)
        self.char_count += len(expanded)

    def _open(self, tag: str, entity_type: str, url: str = "") -> None:
        self.open_entities.append(_OpenEntity(tag, entity_type, self.char_count, url))

    def _close(self, tag: str) -> None:
        for index in range(len(self.open_entities) - 1, -1, -1):
            entry = self.open_entities[index]
            if entry.tag != tag:
                continue
            self.open_entities.pop(index)
            if self.char_count > entry.start:
                self.spans.append(TelegramFormatSpan(entry.type, entry.start, self.char_count, entry.url))
            return

    def finish(self) -> None:
        while self.open_entities:
            entry = self.open_entities.pop()
            if self.char_count > entry.start:
                self.spans.append(TelegramFormatSpan(entry.type, entry.start, self.char_count, entry.url))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.casefold()
        if name == "a":
            href = _safe_href(next((value for key, value in attrs if key.casefold() == "href"), ""))
            if href:
                self._open("a", "text_link", href)
            return
        if name == "blockquote":
            expandable = any(key.casefold() == "expandable" for key, _ in attrs)
            self._open("blockquote", "expandable_blockquote" if expandable else "blockquote")
            return
        entity_type = _ENTITY_TYPES.get(name)
        if entity_type:
            self._open(name, entity_type)

    def handle_endtag(self, tag: str) -> None:
        self._close(tag.casefold())

    def handle_data(self, data: str) -> None:
        self._append(data)


def _spans_overlap(first: TelegramFormatSpan, second: TelegramFormatSpan) -> bool:
    return first.start < second.end and second.start < first.end


def _trim_span(span: TelegramFormatSpan, text: str) -> TelegramFormatSpan | None:
    end = span.end
    while end > span.start and text[end - 1].isspace():
        end -= 1
    if end <= span.start:
        return None
    if end == span.end:
        return span
    return TelegramFormatSpan(span.type, span.start, end, span.url)


def _sanitize_spans(spans: list[TelegramFormatSpan], text: str) -> list[TelegramFormatSpan]:
    trimmed = [candidate for span in spans if (candidate := _trim_span(span, text)) is not None]

    hard_exclusive: list[TelegramFormatSpan] = []
    for span in sorted(
        (item for item in trimmed if item.type in _HARD_EXCLUSIVE_ENTITY_TYPES),
        key=lambda item: (item.start, -(item.end - item.start), item.type),
    ):
        if not any(_spans_overlap(span, other) for other in hard_exclusive):
            hard_exclusive.append(span)

    candidates = [
        span
        for span in trimmed
        if span.type not in _HARD_EXCLUSIVE_ENTITY_TYPES
        and not any(_spans_overlap(span, other) for other in hard_exclusive)
    ]
    non_style: list[TelegramFormatSpan] = []
    for span in sorted(
        (item for item in candidates if item.type not in _STYLE_ENTITY_TYPES),
        key=lambda item: (item.start, -(item.end - item.start), item.type, item.url),
    ):
        if not any(_spans_overlap(span, other) for other in non_style):
            non_style.append(span)

    result = [
        *hard_exclusive,
        *non_style,
        *(item for item in candidates if item.type in _STYLE_ENTITY_TYPES),
    ]
    return sorted(result, key=lambda item: (item.start, -(item.end - item.start), item.type, item.url))


def telegram_safe_output(text: str) -> str:
    """Strip presentation HTML outside code while preserving readable structure."""
    source = str(text or "")
    if "<" not in source and "&" not in source:
        return source
    masked, protected = _mask_literals(source, "TGCODE")
    parser = _PlainTelegramHTML()
    try:
        parser.feed(masked)
        parser.close()
        result = "".join(parser.parts)
    except Exception:
        return source
    result = re.sub(r"[ \t]+\n", "\n", result)
    result = re.sub(r"\n(?=[.,!?;:])", "", result)
    result = re.sub(r"\n{3,}", "\n\n", result).strip()
    return _restore_literals(result, protected, "TGCODE")


def telegram_transport_output(text: str) -> str:
    """Retain only Telegram-safe HTML tags while flattening all other presentation HTML."""
    source = str(text or "")
    if "<" not in source and "&" not in source:
        return source
    masked, protected = _mask_literals(source, "TGTRANSPORT")
    parser = _TransportTelegramHTML()
    try:
        parser.feed(masked)
        parser.close()
        parser.finish()
        result = "".join(parser.parts)
    except Exception:
        return telegram_safe_output(source)
    result = re.sub(r"[ \t]+\n", "\n", result)
    result = re.sub(r"\n(?=[.,!?;:])", "", result)
    result = re.sub(r"\n{3,}", "\n\n", result).strip()
    return _restore_literals(result, protected, "TGTRANSPORT")


def telegram_format_spans(text: str) -> tuple[str, list[TelegramFormatSpan]]:
    """Return visible text plus allowlisted Telegram formatting spans."""
    transport = telegram_transport_output(text)
    masked, protected = _mask_literals(transport, "TGFMT")
    parser = _EntityTelegramHTML(protected, "TGFMT")
    try:
        parser.feed(masked)
        parser.close()
        parser.finish()
    except Exception:
        return telegram_safe_output(transport), []
    visible = "".join(parser.parts)
    return visible, _sanitize_spans(parser.spans, visible)
