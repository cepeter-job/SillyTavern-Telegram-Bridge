"""Lossless, scope-preserving normalization of oversized classified summary JSON.

The established artifact contract is still <=32 blocks. Only a bounded helper
response with independently valid original per-block classifications may be
coalesced; no text, audience or ordering is silently dropped or rewritten.
"""

from __future__ import annotations

from itertools import pairwise
from typing import Any

from bridge.limits import SUMMARY_MAX_CHARS
from bridge.memory_artifact_store import parse_classified_blocks

MAX_RAW_SUMMARY_BLOCKS = 64
MAX_ARTIFACT_BLOCKS = 32
MAX_SINGLE_BLOCK_CHARS = 5000

# Prefer headroom so accepting one source part does not immediately saturate
# the next update. The unchanged hard limit stays in the artifact validator.
SUMMARY_TARGET_CHARS = SUMMARY_MAX_CHARS * 9 // 10
SUMMARY_PRESSURE_CHARS = SUMMARY_MAX_CHARS * 4 // 5
SUMMARY_SHORT_LENGTH_CONTRACT = (
    "Return no more than 32 classified blocks in the complete JSON object. "
    "Merge related facts only when visibility and known_by are identical, "
    "and preserve all distinct facts, promises, and causal links. "
    f"Their combined block text including newline separators must be at most {SUMMARY_MAX_CHARS:,} characters. "
)

# Concrete, mutually exclusive examples help JSON-only utility models avoid
# internally contradictory audience fields. These are *format*, not story facts.
# Persisted classification still goes through the unchanged fail-closed validator.
SUMMARY_AUDIENCE_OUTPUT_CONTRACT = (
    "AUDIENCE OUTPUT SHAPES (examples are syntax only, never story facts): "
    '{"text":"The bell rings.","visibility":"shared","known_by":[]} '
    'or {"text":"Ada privately hides the map.","visibility":"restricted","known_by":["Ada"]}. '
    "Decide knowers from canonical source evidence first. A nonempty known_by MUST use restricted "
    "visibility, never shared. A shared block MUST have known_by=[], even when its text names people. "
    "Every restricted block requires at least one source-supported named knower; never infer others. "
    "If private knowers cannot be grounded, never expose the fact as shared. "
    "Check each block against these exclusive patterns before sending JSON."
)
SUMMARY_COMPACTION_CONTRACT = (
    "Complete updated summary JSON must contain no more than 32 classified blocks. "
    f"Their combined text including newline separators must be at most {SUMMARY_MAX_CHARS:,} characters; "
    f"aim for at most {SUMMARY_TARGET_CHARS:,} characters to leave room for future source parts. "
    "Count characters across ALL blocks, not each block or the serialized JSON. "
    "Rewrite repeated descriptions and redundant wording compactly, but do not silently drop "
    "distinct established facts, exact names, negations, promises, causal dependencies, "
    "unresolved commitments, branch boundaries, or who knows a secret. "
    "Keep shared and restricted visibility/known_by separate; never widen a private audience. "
    "Never invent continuity or claim a source part was accepted without a valid complete summary."
)


def summary_length_contract(previous: dict[str, Any]) -> str:
    """Only near-full accumulators need the longer compression guidance."""
    blocks = previous.get("blocks")
    if not isinstance(blocks, list):
        return SUMMARY_COMPACTION_CONTRACT
    total = 0
    for block in blocks:
        if not isinstance(block, dict) or not isinstance(block.get("text"), str):
            return SUMMARY_COMPACTION_CONTRACT
        total += len(block["text"])
    return SUMMARY_COMPACTION_CONTRACT if total >= SUMMARY_PRESSURE_CHARS else SUMMARY_SHORT_LENGTH_CONTRACT


def coalesce_summary_response(payload: dict[str, Any]) -> dict[str, Any]:
    """Pack excess adjacent same-audience blocks without losing canonical text.

    A mixed audience, oversized source, or unmergeable collection fails closed.
    Return untouched payload for the already accepted <=32-block contract.
    """
    raw_blocks = payload.get("blocks")
    if not isinstance(raw_blocks, list) or len(raw_blocks) <= MAX_ARTIFACT_BLOCKS:
        return payload
    if len(raw_blocks) > MAX_RAW_SUMMARY_BLOCKS:
        raise ValueError("Memory classification requires at most 32 explicit blocks")

    # Validate each original block and its audience *before* any coalescing.
    # The published artifact validator still independently enforces all limits.
    packed = [parse_classified_blocks([raw])[0] for raw in raw_blocks]
    if sum(len(item["text"]) for item in packed) + len(packed) - 1 > SUMMARY_MAX_CHARS:
        raise ValueError("Classified summary requires bounded, nonempty output")

    while len(packed) > MAX_ARTIFACT_BLOCKS:
        eligible = [
            (len(left["text"]) + 1 + len(right["text"]), index)
            for index, (left, right) in enumerate(pairwise(packed))
            if left["visibility"] == right["visibility"]
            and left["known_by"] == right["known_by"]
            and len(left["text"]) + 1 + len(right["text"]) <= MAX_SINGLE_BLOCK_CHARS
        ]
        if not eligible:
            raise ValueError("Memory classification requires at most 32 explicit blocks")
        _, index = min(eligible)
        packed[index] = {
            "text": packed[index]["text"] + "\n" + packed[index + 1]["text"],
            "visibility": packed[index]["visibility"],
            "known_by": packed[index]["known_by"],
        }
        del packed[index + 1]
    # One last independent validator enforces the immutable 32-block contract.
    return dict(payload, blocks=parse_classified_blocks(packed))
