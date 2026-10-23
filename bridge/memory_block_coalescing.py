"""Lossless adjacent packing and shared classified artifact capacity limits."""

from __future__ import annotations

from itertools import pairwise
from typing import Any

MAX_ARTIFACT_BLOCKS = 32
MAX_SINGLE_BLOCK_CHARS = 5000


def coalesce_adjacent_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """Pack already validated blocks without changing their text, order or audience.

    Callers own raw input and aggregate output limits. A missing result means
    no eligible adjacent merge remains within the per-block and audience bounds.
    The caller's blocks remain untouched even when this packing fails.
    """
    packed = list(blocks)
    while len(packed) > MAX_ARTIFACT_BLOCKS:
        eligible = [
            (len(left["text"]) + 1 + len(right["text"]), index)
            for index, (left, right) in enumerate(pairwise(packed))
            if left["visibility"] == right["visibility"]
            and left["known_by"] == right["known_by"]
            and len(left["text"]) + 1 + len(right["text"]) <= MAX_SINGLE_BLOCK_CHARS
        ]
        if not eligible:
            return None
        _, index = min(eligible)
        packed[index] = {
            "text": packed[index]["text"] + "\n" + packed[index + 1]["text"],
            "visibility": packed[index]["visibility"],
            "known_by": packed[index]["known_by"],
        }
        del packed[index + 1]
    return packed
