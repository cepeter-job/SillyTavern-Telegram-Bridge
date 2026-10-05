"""Small lexical fallback shared by source-authorized native memory reads."""

import re

QUERY_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "at",
        "for",
        "from",
        "has",
        "have",
        "how",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "who",
        "why",
        "with",
        "you",
        "your",
    }
)


def relevance_terms(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[A-Za-z0-9_'-]+", str(value or "").casefold(), flags=re.UNICODE)
        if len(token) >= 2 and token not in QUERY_STOPWORDS
    }
