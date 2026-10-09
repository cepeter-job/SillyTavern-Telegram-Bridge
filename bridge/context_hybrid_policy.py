"""Conservative lexical anchors for a shadow proposal, never a semantic proof."""

from __future__ import annotations

import re
import unicodedata

from bridge.context_hybrid_types import HybridOptions, HybridRow
from bridge.memory_contracts import relevance_terms

_PATTERNS = {
    "negation": (
        r"\b(?:no|not|never|neither|without|cannot|can't|won't|don't|didn't|isn't|wasn't|"
        r"tidak|bukan|belum|jangan|tak|nggak)\b"
    ),
    "commitment": (
        r"\b(?:promis\w*|swear|swore|owe\w*|debt|must|unless|until|consent|refus\w*|"
        r"forbid\w*|forbidden|agree\w*|accept\w*|permission|janji|berjanji|utang|hutang|"
        r"harus|sampai|setuju|menolak|tolak|izin)\b"
    ),
    "causal_branch": (
        r"\b(?:because|therefore|caused|consequence\w*|resulted|chose|choose|chosen|choice|"
        r"branch|instead|karena|sebab|akibat|sehingga|pilih\w*|memilih|pilihan|cabang)\b"
    ),
    "knowledge_callback": (
        r"\b(?:secret\w*|privat\w*|know\w*|knew|learn\w*|remember\w*|forgot\w*|later|"
        r"callback|trust|relationship|rahasia|tahu|mengetahui|ingat|lupa|nanti|percaya|"
        r"hubungan)\b"
    ),
}
_ANCHORS = {kind: re.compile(pattern, re.IGNORECASE) for kind, pattern in _PATTERNS.items()}
_AMBIGUOUS = re.compile(
    r"^(?:yes|no|maybe|ok|okay|sure|i agree|i refuse|i accept|that one|this one|"
    r"ya|iya|baik|tidak|setuju|(?:option|choice|pilihan)\s*[a-d1-4]|[a-d1-4])[.!? ]*$",
    re.I,
)


def anchor_kinds(text: str) -> frozenset[str]:
    normalized = text.replace("’", "'")
    kinds = {kind for kind, pattern in _ANCHORS.items() if pattern.search(normalized)}
    if _AMBIGUOUS.fullmatch(normalized.strip()):
        kinds.add("ambiguous_reply")
    return frozenset(kinds)


def supported_script(text: str) -> bool:
    # English/Indonesian lexical anchors do not authorize ignoring other scripts.
    return all(not char.isalpha() or "LATIN" in unicodedata.name(char, "") for char in text)


def retained_indices(rows: tuple[HybridRow, ...], query: str, options: HybridOptions) -> tuple[set[int], dict]:
    count = len(rows)
    recent = set(range(max(0, count - options.recent_turns), count))
    protected = {0, *recent}
    anchors = {index for index, row in enumerate(rows) if anchor_kinds(row.text)}
    terms = relevance_terms(query)
    scored = [(len(relevance_terms(row.text) & terms), index) for index, row in enumerate(rows) if index not in recent]
    related = {index for score, index in sorted(scored, reverse=True)[: options.relevant_turns] if score > 0}
    # Keep the referent next to a reply/commitment or query match, never just a
    # bare yes/no orphan. Multiplicity and chronological order remain intact.
    for index in anchors | related:
        protected.update(range(max(0, index - options.neighbors), min(count, index + options.neighbors + 1)))
    return protected, {"anchor_turns": len(anchors), "relevant_turns": len(related), "recent_turns": len(recent)}
