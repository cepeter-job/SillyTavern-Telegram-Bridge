"""Read-only, bounded prefix stability and repeated-instruction diagnostics.

A stable text prefix is neither a provider cache hit nor authorization to hoist
or delete instructions. All observations are explicitly supplied by the caller;
this module installs no runtime hooks, persists nothing and performs no I/O.
"""

from __future__ import annotations

import math
import secrets
from collections import Counter, deque
from dataclasses import dataclass, field

from bridge.prompt_profile_snapshot import CHUNK_CHARACTERS, INSTRUCTIONS, PromptSnapshot, snapshot

MAX_FINGERPRINT_UNITS = 65536


def _units(sample: PromptSnapshot) -> int:
    return 2 + len(sample.sections) + sum(2 + len(m.chunks) for m in sample.messages)


def _prefix(samples: list[PromptSnapshot]) -> tuple[int, int, int | None]:
    if len(samples) < 2:
        return 0, 0, None
    if len({s.envelope for s in samples}) != 1:
        return 0, 0, 0
    complete, characters = 0, 0
    for position in range(min(len(s.messages) for s in samples)):
        column = [s.messages[position] for s in samples]
        if any(m.role not in INSTRUCTIONS for m in column):
            return complete, characters, None
        if len({m.digest for m in column}) == 1:
            complete += 1
            characters += column[0].characters
            continue
        if all(m.plain_text for m in column) and len({m.layout for m in column}) == 1:
            for blocks in zip(*(m.chunks for m in column), strict=False):
                if len(set(blocks)) != 1:
                    break
                characters += CHUNK_CHARACTERS
        return complete, characters, position
    return complete, characters, None


def _positions(samples: list[PromptSnapshot], prefix_messages: int) -> list[dict]:
    result = []
    for position in range(max(len(s.messages) for s in samples)):
        column = [s.messages[position] for s in samples if len(s.messages) > position]
        stable = len(samples) >= 2 and len(column) == len(samples) and len({m.digest for m in column}) == 1
        roles = {m.role for m in column}
        result.append(
            {
                "position": position,
                "appearances": len(column),
                "sample_count": len(samples),
                "modal_identical_count": max(Counter(m.digest for m in column).values()),
                "stable": stable,
                "role": next(iter(roles)) if len(roles) == 1 else "mixed",
                "characters_min": min(m.characters for m in column),
                "characters_max": max(m.characters for m in column),
                "blocked_by_earlier_variation": stable and roles.issubset(INSTRUCTIONS) and position >= prefix_messages,
            }
        )
    return result


def _section_report(samples: list[PromptSnapshot]) -> list[dict]:
    columns: dict[tuple[str, int], list] = {}
    for sample in samples:
        for section in sample.sections:
            columns.setdefault((section.kind, section.ordinal), []).append(section)
    result = []
    for (kind, ordinal), column in sorted(columns.items()):
        counts = Counter(section.digest for section in column)
        result.append(
            {
                "kind": kind,
                "ordinal": ordinal,
                "appearances": len(column),
                "sample_count": len(samples),
                "distinct_fingerprints": len(counts),
                "modal_identical_count": max(counts.values()),
                "stable": len(samples) >= 2 and len(column) == len(samples) and len(counts) == 1,
                "characters_min": min(s.characters for s in column),
                "characters_max": max(s.characters for s in column),
            }
        )
    return result


def _duplicates(sample: PromptSnapshot, ratio: float) -> list[dict]:
    groups: dict[str, list[int]] = {}
    for index, message in enumerate(sample.messages):
        if message.role in INSTRUCTIONS and message.characters:
            groups.setdefault(message.digest, []).append(index)
    return [
        {
            "positions": positions,
            "occurrences": len(positions),
            "fingerprint": digest,
            "repeated_text_characters": sample.messages[positions[0]].characters * (len(positions) - 1),
            "repeated_text_tokens_estimate": math.ceil(
                sample.messages[positions[0]].characters * (len(positions) - 1) / ratio
            ),
            "removal_authorized": False,
            "reason": "exact_payload_only_requires_semantic_and_placement_review",
        }
        for digest, positions in groups.items()
        if len(positions) > 1
    ]


def _usage_report(samples: list[PromptSnapshot]) -> dict:
    # An arbitrary builder replay cannot stand in for real provider accounting.
    observed = [s.usage if s.stage == "provider" else (None, None, None) for s in samples]
    counts = [sum(row[i] is not None for row in observed) for i in range(3)]
    sums = [sum(row[i] or 0 for row in observed) if counts[i] else None for i in range(3)]
    return {
        "input_samples": counts[0],
        "unknown_input_samples": len(samples) - counts[0],
        "input_tokens_sum": sums[0],
        "cached_samples": counts[1],
        "cached_tokens_sum": sums[1],
        "output_samples": counts[2],
        "output_tokens_sum": sums[2],
        "complete_window_input_tokens": sums[0] if counts[0] == len(samples) else None,
        "source": "caller_supplied_provider_usage_not_independently_verified",
        "accounting": "request_usage_only_not_accepted_work",
    }


@dataclass
class _Cohort:
    samples: deque[PromptSnapshot]
    seen: int = 0


@dataclass(repr=False)
class PrefixProfiler:
    """Bounded recent observations; fresh secret prevents cross-run hash guessing.

    Scope keys must encode session incarnation, character configuration, reader
    policy and branch/rewrite identity as well as provider route/model and stage.
    The profiler checks identity equality, not authentication or source validity.
    """

    window: int = 16
    max_cohorts: int = 16
    chars_per_token: float = 4.0
    _key: bytes = field(default_factory=lambda: secrets.token_bytes(32), init=False, repr=False)
    _cohorts: dict[str, _Cohort] = field(default_factory=dict, init=False, repr=False)
    _retained_units: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        if type(self.window) is not int or not 2 <= self.window <= 32:
            raise ValueError("profile_invalid_window")
        if type(self.max_cohorts) is not int or not 1 <= self.max_cohorts <= 16:
            raise ValueError("profile_invalid_cohort_limit")
        if (
            type(self.chars_per_token) not in (int, float)
            or not math.isfinite(self.chars_per_token)
            or not 1 <= self.chars_per_token <= 16
        ):
            raise ValueError("profile_invalid_token_ratio")

    def observe(self, request: dict, scope: dict, *, usage: dict | None = None) -> None:
        captured = snapshot(request, scope, usage, self._key)
        cohort = self._cohorts.get(captured.scope)
        if cohort is None and len(self._cohorts) >= self.max_cohorts:
            raise ValueError("profile_cohort_limit")
        retired = cohort.samples[0] if cohort and len(cohort.samples) == cohort.samples.maxlen else None
        next_units = self._retained_units + _units(captured) - (_units(retired) if retired else 0)
        if next_units > MAX_FINGERPRINT_UNITS:
            raise ValueError("profile_fingerprint_limit")
        if cohort is None:
            cohort = _Cohort(deque(maxlen=self.window))
            self._cohorts[captured.scope] = cohort
        cohort.samples.append(captured)
        cohort.seen += 1
        self._retained_units = next_units

    def report(self) -> dict:
        groups = []
        for identifier, cohort in self._cohorts.items():
            samples = list(cohort.samples)
            whole, characters, varying = _prefix(samples)
            total = sum(s.text_characters for s in samples)
            groups.append(
                {
                    "scope_fingerprint": identifier,
                    "stage": samples[0].stage,
                    "observations_seen": cohort.seen,
                    "retained_samples": len(samples),
                    "evicted_samples": cohort.seen - len(samples),
                    "comparison_status": "observed" if len(samples) >= 2 else "insufficient_samples",
                    "request_envelope_stable": len(samples) >= 2 and len({s.envelope for s in samples}) == 1,
                    "stable_prefix_messages": whole,
                    "stable_prefix_characters": characters,
                    "stable_prefix_text_tokens_estimate": math.ceil(characters / self.chars_per_token),
                    "observed_prefix_text_share": characters * len(samples) / total if total else 0.0,
                    "first_varying_instruction_position": varying,
                    "text_tokens_estimate_sum": sum(
                        math.ceil(s.text_characters / self.chars_per_token) for s in samples
                    ),
                    "non_text_payload_present": any(s.non_text for s in samples),
                    "full_prompt_token_count_known": False,
                    "positions": _positions(samples, whole),
                    "sections": _section_report(samples),
                    "latest_instruction_duplicates": _duplicates(samples[-1], self.chars_per_token),
                    "provider_usage": _usage_report(samples),
                }
            )
        return {
            "schema_version": 1,
            "type": "read_only_prompt_prefix_profile",
            "fingerprints": "ephemeral_hmac_sha256_key_never_exported",
            "prefix_block_characters": CHUNK_CHARACTERS,
            "recent_window": self.window,
            "retained_fingerprint_units": self._retained_units,
            "max_fingerprint_units": MAX_FINGERPRINT_UNITS,
            "max_cohorts": self.max_cohorts,
            "chars_per_token": self.chars_per_token,
            "cohorts": groups,
            "prompt_mutations": 0,
            "optimization_authorized": False,
            "provider_savings_fraction": None,
            "accepted_work_savings_fraction": None,
            "cache_hit_rate_prediction": None,
            "limitations": [
                "Prefix characters are a conservative textual lower bound, not provider token-cache eligibility.",
                "Estimates exclude framing, non-text payloads and tools; no full-wire size is asserted.",
                "Scope/stage/usage are caller declarations; identities are isolated but not authenticated here.",
                "Exact repeated instructions still require provenance, placement and semantic review before deletion.",
                "Section provenance is limited to validated native optional spans; remainders are not assumed static.",
            ],
        }
