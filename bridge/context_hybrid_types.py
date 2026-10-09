"""Immutable values for hybrid shadow evaluation, not dispatch authorization."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from bridge.memory_contracts import MemoryReadScope

HISTORY_MARKER = "_context_history_index"
SHADOW_MARKER = "_context_hybrid_shadow_only"
MAX_HISTORY_ROWS = 256
MAX_SOURCE_CHARS = 950000
MAX_WINDOWS = 64
MAX_SUMMARY_CHARS = 128000


@dataclass(frozen=True)
class HybridOptions:
    recent_turns: int = 8
    relevant_turns: int = 4
    neighbors: int = 1
    chars_per_token: float = 4.0

    def __post_init__(self) -> None:
        for name, low, high in (("recent_turns", 8, 256), ("relevant_turns", 0, 16), ("neighbors", 1, 2)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError("hybrid_options_invalid")
        if (
            type(self.chars_per_token) not in (int, float)
            or not math.isfinite(self.chars_per_token)
            or self.chars_per_token <= 0
        ):
            raise ValueError("hybrid_token_ratio_invalid")


@dataclass(frozen=True)
class HybridRow:
    row_id: int
    role: str
    text: str = field(repr=False)
    created_at: float


@dataclass(frozen=True)
class HybridBlock:
    text: str = field(repr=False)
    visibility: str
    known_by: tuple[str, ...] = field(repr=False)


@dataclass(frozen=True)
class HybridWindow:
    through_rowid: int
    blocks: tuple[HybridBlock, ...] = field(repr=False)
    source_document: str = field(repr=False)
    digest: str = field(repr=False)


@dataclass(frozen=True)
class HybridSnapshot:
    scope: MemoryReadScope = field(repr=False)
    rows: tuple[HybridRow, ...] = field(repr=False)
    windows: tuple[HybridWindow, ...] = field(repr=False)
    fingerprint: str


@dataclass(frozen=True)
class HybridShadowResult:
    # Candidate material is ONLY for an explicitly requested offline review.
    # The application receives the original baseline, never this candidate.
    dispatch_messages: list[dict] = field(repr=False)
    candidate_messages: list[dict] = field(repr=False)
    metrics: dict[str, object]


SHADOW_REASONS = frozenset(
    {
        "already_a_shadow_candidate",
        "reader_or_source_changed",
        "insufficient_older_history",
        "unsupported_older_script",
        "all_history_protected",
        "protected_payload_changed",
        "no_savings",
        "source_or_archive_changed",
        "semantic_review_required",
        "native_evidence_missing_or_changed",
    }
)


def public_shadow_metrics(observed: dict, baseline_tokens: int) -> dict[str, object]:
    """Whitelist scalar telemetry; never propagate arbitrary callback text."""
    if (
        not isinstance(observed, dict)
        or observed.get("reason") not in SHADOW_REASONS
        or type(observed.get("candidate_tokens")) is not int
        or not 0 <= observed["candidate_tokens"] <= baseline_tokens
        or type(observed.get("omitted_turns")) is not int
        or not 0 <= observed["omitted_turns"] <= MAX_HISTORY_ROWS
        or type(observed.get("native_source_verified")) is not bool
    ):
        raise ValueError("hybrid_shadow_diagnostics_invalid")
    extras: dict[str, object] = {}
    if "statement_candidate_tokens" in observed:
        reason = observed.get("statement_reason")
        value = observed.get("statement_candidate_tokens")
        source = observed.get("statement_source_verified")
        allowed = SHADOW_REASONS | {
            "existing_shadow_candidate",
            "source_or_required_evidence_unavailable",
            "source_or_summary_changed",
        }
        if (
            reason not in allowed
            or type(value) is not int
            or not 0 <= value <= baseline_tokens
            or type(source) is not bool
        ):
            raise ValueError("statement_shadow_metrics_invalid")
        extras = {
            "hybrid_statement_reason": reason,
            "hybrid_statement_candidate_tokens": value,
            "hybrid_statement_source_verified": source,
            "hybrid_statement_activation_allowed": False,
        }
    return {
        **extras,
        "hybrid_shadow_reason": observed["reason"],
        "hybrid_shadow_candidate_tokens": observed["candidate_tokens"],
        "hybrid_shadow_omitted_turns": observed["omitted_turns"],
        "hybrid_shadow_source_verified": observed["native_source_verified"],
        "hybrid_shadow_activation_allowed": False,
    }
