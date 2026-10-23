"""Bounded scene response normalization before canonical artifact validation."""

from __future__ import annotations

from typing import Any

from bridge.extraction_contracts import SceneContractError
from bridge.memory_artifact_store import parse_classified_blocks
from bridge.memory_block_coalescing import MAX_ARTIFACT_BLOCKS, MAX_SINGLE_BLOCK_CHARS

MAX_RAW_SCENE_BLOCKS = 64
MAX_SCENE_BLOCK_TEXT_CHARS = 32000
_BLOCK_COUNT_ERROR = "Memory classification requires at most 32 explicit blocks"


def _partition_scene_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """Find exactly 32 valid contiguous groups over at most 64 validated blocks.

    Keep one predecessor per reachable endpoint and group count. Unlike a
    shortest-pair merge, this cannot strand a valid alternative partition.
    """
    boundaries: list[dict[int, int]] = [{0: 0}]
    for _ in range(MAX_ARTIFACT_BLOCKS):
        following: dict[int, int] = {}
        for start in boundaries[-1]:
            if start == len(blocks):
                continue
            first = blocks[start]
            text_chars = -1
            for end in range(start, len(blocks)):
                item = blocks[end]
                if item["visibility"] != first["visibility"] or item["known_by"] != first["known_by"]:
                    break
                text_chars += len(item["text"]) + 1
                if text_chars > MAX_SINGLE_BLOCK_CHARS:
                    break
                following.setdefault(end + 1, start)
        boundaries.append(following)
    end = len(blocks)
    if end not in boundaries[-1]:
        return None
    packed = []
    for group_count in range(MAX_ARTIFACT_BLOCKS, 0, -1):
        start = boundaries[group_count][end]
        packed.append(dict(blocks[start], text="\n".join(item["text"] for item in blocks[start:end])))
        end = start
    packed.reverse()
    return packed


def coalesce_scene_response(payload: dict[str, Any]) -> dict[str, Any]:
    """Preserve all valid text and exact scopes while recovering modest overflow.

    Only excess adjacent blocks with identical validated audiences can merge.
    Each original block is validated before any coalescing. The unchanged
    artifact validator remains responsible for the accepted output contract.
    """
    raw_blocks = payload.get("blocks")
    if not isinstance(raw_blocks, list) or len(raw_blocks) <= MAX_ARTIFACT_BLOCKS:
        return payload
    if len(raw_blocks) > MAX_RAW_SCENE_BLOCKS:
        raise SceneContractError(_BLOCK_COUNT_ERROR, "scene_blocks_raw_limit")
    blocks = [parse_classified_blocks([raw])[0] for raw in raw_blocks]
    # Every required merge adds one newline to stored block text. Count these
    # separators before packing so a result at the aggregate limit stays exact.
    required_separators = len(blocks) - MAX_ARTIFACT_BLOCKS
    if sum(len(block["text"]) for block in blocks) + required_separators > MAX_SCENE_BLOCK_TEXT_CHARS:
        raise SceneContractError("Classified memory exceeds the artifact bound", "scene_total_text_limit")
    packed = _partition_scene_blocks(blocks)
    if packed is None:
        raise SceneContractError(_BLOCK_COUNT_ERROR, "scene_blocks_unmergeable")
    return dict(payload, blocks=parse_classified_blocks(packed))
