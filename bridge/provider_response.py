"""Bound and normalize provider responses before transport-specific handling."""

from collections.abc import Iterator
from typing import BinaryIO

from bridge.limits import PROVIDER_TEXT_RESPONSE_MAX_BYTES

_STREAM_LINE_MAX_BYTES = 256 * 1024


def openai_response_choices(result: object) -> list[dict]:
    """Return OpenAI-compatible choices from standard or wrapped responses."""
    if not isinstance(result, dict):
        return []

    top_level = result.get("choices")
    if isinstance(top_level, list) and top_level:
        return [choice for choice in top_level if isinstance(choice, dict)]

    data = result.get("data")
    if isinstance(data, dict):
        nested = data.get("choices")
        if isinstance(nested, list):
            return [choice for choice in nested if isinstance(choice, dict)]

    if isinstance(top_level, list):
        return [choice for choice in top_level if isinstance(choice, dict)]
    return []


def read_provider_response(response: BinaryIO) -> bytes:
    raw = response.read(PROVIDER_TEXT_RESPONSE_MAX_BYTES + 1)
    if len(raw) > PROVIDER_TEXT_RESPONSE_MAX_BYTES:
        raise ValueError("Provider response exceeded the safety limit")
    return raw


def provider_response_lines(response: BinaryIO) -> Iterator[bytes]:
    total = 0
    while True:
        limit = min(_STREAM_LINE_MAX_BYTES, PROVIDER_TEXT_RESPONSE_MAX_BYTES - total)
        raw = response.readline(limit + 1)
        if not raw:
            return
        total += len(raw)
        if len(raw) > limit:
            raise ValueError("Provider response exceeded the safety limit")
        yield raw
