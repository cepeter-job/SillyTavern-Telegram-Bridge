"""Content-free estimates for sections explicitly classified by prompt builders.

These character-based estimates exclude message framing and image tokens. They
are neither provider-reported input usage nor a substitute for transport budget
checks. Category provenance comes from construction, never prompt parsing.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from bridge.context_compaction import DEFAULT_TOKEN_ESTIMATE_CHARS_PER_TOKEN, normalize_token_ratio

SECTION_CATEGORIES = ("mandatory", "history", "world_info", "derived", "task")
BUILDER_METRIC_FIELDS = frozenset({"section_estimated_tokens", "selection_metrics"})
MAX_METRIC_COUNT = 2**31 - 1
_SELECTION_COUNTS = frozenset(
    {
        "original_tokens",
        "candidate_tokens",
        "selected_blocks",
        "deduplicated_blocks",
        "history_shadow_candidate_tokens",
        "history_shadow_reframed_turns",
    }
)
_SELECTION_FLAGS = frozenset({"coverage_valid", "applied"})
_SELECTION_ENUMS = {
    "mode": frozenset({"off", "shadow", "enabled"}),
    "history_shadow_reason": frozenset(
        {
            "historical",
            "invalid_scope",
            "incomplete_coverage",
            "ambiguous",
            "no_savings",
            "selected",
        }
    ),
    "reason": frozenset(
        {
            "off",
            "not_approved",
            "invalid_scope",
            "incomplete_coverage",
            "pending_invalidation",
            "source_changed",
            "historical",
            "ambiguous",
            "no_savings",
            "selected",
        }
    ),
}


def estimate_context_sections(
    sections: Mapping[str, Sequence[str] | str],
    *,
    chars_per_token: float = DEFAULT_TOKEN_ESTIMATE_CHARS_PER_TOKEN,
) -> dict[str, int]:
    """Return five deterministic text-only estimates without retaining any text."""
    if any(category not in SECTION_CATEGORIES for category in sections):
        raise ValueError("Context section category must be allowlisted")
    ratio = normalize_token_ratio(chars_per_token)
    result: dict[str, int] = {}
    for category in SECTION_CATEGORIES:
        texts = sections.get(category, ())
        if isinstance(texts, str):
            texts = (texts,)
        if not isinstance(texts, Sequence) or any(not isinstance(text, str) for text in texts):
            raise TypeError("Context section values must be text or sequences of text")
        result[category] = math.ceil(sum(len(text) for text in texts) / ratio)
    return result


def redact_builder_metrics(stats: dict[str, object]) -> dict[str, object]:
    """Validate bounded, allowlisted metrics at both persistence and read time."""
    result: dict[str, object] = {}
    sections = stats.get("section_estimated_tokens")
    if isinstance(sections, dict):
        counts = {key: value for key, value in sections.items() if key in SECTION_CATEGORIES and _valid_count(value)}
        if counts:
            result["section_estimated_tokens"] = counts
    selection = stats.get("selection_metrics")
    if isinstance(selection, dict):
        selected: dict[str, object] = {}
        for key, value in selection.items():
            if key in _SELECTION_COUNTS and _valid_count(value):
                selected[key] = value
            elif key in _SELECTION_FLAGS and isinstance(value, bool):
                selected[key] = value
            elif key in _SELECTION_ENUMS and isinstance(value, str) and value in _SELECTION_ENUMS[key]:
                selected[key] = value
        if selected:
            result["selection_metrics"] = selected
    return result


def _valid_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_METRIC_COUNT
