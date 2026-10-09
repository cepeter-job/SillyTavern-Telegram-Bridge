"""Exact source-aligned statement receipts for evaluation-only context proposals."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from bridge.context_hybrid_policy import anchor_kinds, supported_script
from bridge.context_hybrid_types import HybridOptions, HybridRow
from bridge.memory_contracts import relevance_terms

_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Þ0-9])")
_UNSAFE = re.compile(r"[\x60{}<>]|(?:\b(?:mr|ms|dr|prof)\.)", re.IGNORECASE)
_SHORT_ANSWER = re.compile(
    r"^(?:yes|no|yeah|nope|not yet|okay|sure|i agree|i decline|"
    r"option [a-d1-4]|pilihan [a-d1-4]|ya|iya|tidak|setuju|[a-d1-4])[.!? ]*$",
    re.IGNORECASE,
)
MAX_SPANS = 512
MAX_STATEMENT_CHARS = 60000


@dataclass(frozen=True)
class StatementSpan:
    start: int
    end: int
    text: str = field(repr=False)
    sha256: str = field(repr=False)


@dataclass(frozen=True)
class StatementSelection:
    selected: tuple[tuple[int, tuple[StatementSpan, ...]], ...]
    counts: dict[str, int]


def split_complete_sentences(text: str) -> tuple[tuple[int, int], ...] | None:
    """Keep roleplay quotes and stage directions as atomic exact paragraphs."""
    if not text or not supported_script(text) or _UNSAFE.search(text) or "\r" in text:
        return None
    paragraphs = [(0, len(text))]
    if "\n" in text:
        paragraphs = []
        start = 0
        for match in re.finditer(r"\n+", text):
            if text[start : match.start()].strip():
                paragraphs.append((start, match.start()))
            start = match.end()
        if text[start:].strip():
            paragraphs.append((start, len(text)))
    if len(paragraphs) > 128:
        return None
    result: list[tuple[int, int]] = []
    for begin, end in paragraphs:
        part = text[begin:end]
        if (
            part.count('"') % 2
            or part.count("*") % 2
            or part.count("“") != part.count("”")
            or part.count("‘") != part.count("’")
        ):
            return None
        # Mixed dialogue and stage directions are never clipped mid-quote.
        if any(char in part for char in ('"', "“", "”", "*", "‘", "’")):
            result.append((begin, end))
            continue
        if part.lstrip().startswith(("-", "•", "#", ">")):
            result.append((begin, end))
            continue
        offset = begin
        for match in _BOUNDARY.finditer(part):
            split = begin + match.start()
            if split <= offset:
                return None
            result.append((offset, split))
            offset = begin + match.end()
        result.append((offset, end))
    if len(result) > 256 or any(not text[a:b].strip() for a, b in result):
        return None
    return tuple(result)


def _span(row: HybridRow, start: int, end: int) -> StatementSpan:
    if not 0 <= start < end <= len(row.text):
        raise ValueError("statement_source_offset_invalid")
    exact = row.text[start:end]
    return StatementSpan(start, end, exact, hashlib.sha256(exact.encode("utf-8")).hexdigest())


def validate_statement_receipts(rows: tuple[HybridRow, ...], receipts: StatementSelection) -> bool:
    """Recompute exact substrings and SHA256 against authenticated history."""
    prior = -1
    for index, spans in receipts.selected:
        if type(index) is not int or not prior < index < len(rows) or not spans:
            raise ValueError("statement_receipt_order_invalid")
        row = rows[index]
        last = -1
        for span in spans:
            if (
                not isinstance(span, StatementSpan)
                or not 0 <= span.start < span.end <= len(row.text)
                or span.start < last
                or row.text[span.start : span.end] != span.text
                or hashlib.sha256(span.text.encode("utf-8")).hexdigest() != span.sha256
            ):
                raise ValueError("statement_source_receipt_invalid")
            last = span.end
        prior = index
    return True


def select_statement_spans(rows: tuple[HybridRow, ...], query: str, options: HybridOptions) -> StatementSelection:
    """Select chronological exact critical spans with local referents."""
    if len(rows) <= options.recent_turns + 3:
        raise ValueError("statement_insufficient_older_history")
    prefix = len(rows) - options.recent_turns
    if any(not supported_script(row.text) for row in rows[:prefix]):
        raise ValueError("statement_unsupported_script")
    sentences: list[tuple[tuple[int, int], ...]] = []
    for row in rows[:prefix]:
        parts = split_complete_sentences(row.text)
        sentences.append(parts if parts is not None else ((0, len(row.text)),))
    terms = relevance_terms(query)
    scored = []
    for index, parts in enumerate(sentences):
        for ordinal, (start, end) in enumerate(parts):
            overlap = len(relevance_terms(rows[index].text[start:end]) & terms) if terms else 0
            if overlap:
                scored.append((overlap, index, ordinal))
    relevant = {(index, ordinal) for _, index, ordinal in sorted(scored, reverse=True)[: options.relevant_turns]}
    protected: dict[int, set[int]] = {}
    full_turns: set[int] = {0}
    critical_count = 0
    for index, row in enumerate(rows[:prefix]):
        parts = sentences[index]
        if _SHORT_ANSWER.fullmatch(row.text.strip()) and index:
            full_turns.add(index)
            if rows[index - 1].role == "assistant":
                full_turns.add(index - 1)
        for ordinal, (start, end) in enumerate(parts):
            kinds = anchor_kinds(row.text[start:end])
            if kinds or (index, ordinal) in relevant:
                critical_count += bool(kinds)
                required = protected.setdefault(index, set())
                required.add(ordinal)
                for distance in range(1, options.neighbors + 1):
                    if ordinal >= distance:
                        required.add(ordinal - distance)
                    if ordinal + distance < len(parts):
                        required.add(ordinal + distance)
    selected = []
    for index, row in enumerate(rows[:prefix]):
        ranges = (
            [(0, len(row.text))]
            if index in full_turns
            else [sentences[index][i] for i in sorted(protected.get(index, ()))]
        )
        if ranges:
            selected.append((index, tuple(_span(row, a, b) for a, b in ranges)))
    span_count = sum(len(items) for _, items in selected)
    char_count = sum(span.end - span.start for _, items in selected for span in items)
    if span_count > MAX_SPANS or char_count > MAX_STATEMENT_CHARS:
        raise ValueError("statement_mandatory_source_bound")
    result = StatementSelection(
        tuple(selected),
        {
            "source_statement_spans": span_count,
            "source_statement_characters": char_count,
            "critical_statement_spans": critical_count,
            "query_statement_matches": len(relevant),
            "recent_verbatim_turns": options.recent_turns,
            "older_turns_with_kept_statements": len(selected),
        },
    )
    validate_statement_receipts(rows, result)
    return result
