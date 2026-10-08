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
