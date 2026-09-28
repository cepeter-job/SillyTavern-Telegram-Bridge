"""Pure, content-free usage values. Provider totals are never text estimates."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None
    final: bool = True

    @property
    def complete(self) -> bool:
        return self.final and self.input_tokens is not None and self.output_tokens is not None

    @property
    def reported(self) -> bool:
        return any(value is not None for value in (self.input_tokens, self.output_tokens, self.total_tokens))


@dataclass(frozen=True)
class UsageScope:
    chat_id: str
    session_id: str
    purpose: str


@dataclass(frozen=True)
class UsageEvent:
    scope: UsageScope
    model: str
    readings: tuple[TokenUsage, ...]
    elapsed_ms: int
    status: str


UsageCallback = Callable[[TokenUsage], None]
UsageRecorder = Callable[[UsageEvent], None]


def _count(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= 1_000_000_000 else None


def _mapping(value: object) -> dict:
    return value if isinstance(value, dict) else {}


class UsageCapture:
    """One HTTP response: cumulative stream snapshots replace, never add."""

    def __init__(self, callback: UsageCallback | None = None, *, flavor: str = "openai") -> None:
        self.callback = callback
        self.flavor = flavor
        self.values: dict[str, int] = {}
        self.final = True

    def __enter__(self) -> UsageCapture:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        self.final = self.final and exc_type is None
        if self.callback is not None:
            self.callback(self.snapshot())

    def observe(self, payload: object) -> None:
        data = _mapping(payload)
        usage = _mapping(data.get("usage"))
        if not usage:
            for key in ("data", "response", "message"):
                usage = _mapping(_mapping(data.get(key)).get("usage"))
                if usage:
                    break
        if not usage:
            return
        inputs = _mapping(usage.get("prompt_tokens_details") or usage.get("input_tokens_details"))
        outputs = _mapping(usage.get("completion_tokens_details") or usage.get("output_tokens_details"))
        fields = {
            "input": usage.get("prompt_tokens", usage.get("input_tokens")),
            "output": usage.get("completion_tokens", usage.get("output_tokens")),
            "total": usage.get("total_tokens"),
            "cached": usage.get("cache_read_input_tokens", inputs.get("cached_tokens")),
            "cache_creation": usage.get("cache_creation_input_tokens"),
            "reasoning": outputs.get("reasoning_tokens"),
        }
        for key, value in fields.items():
            count = _count(value)
            if count is not None:
                self.values[key] = count

    def snapshot(self) -> TokenUsage:
        input_tokens = self.values.get("input")
        output_tokens = self.values.get("output")
        cached = self.values.get("cached")
        reasoning = self.values.get("reasoning")
        if self.flavor == "anthropic" and input_tokens is not None:
            input_tokens += self.values.get("cached", 0) + self.values.get("cache_creation", 0)
        total = self.values.get("total")
        if input_tokens is not None and output_tokens is not None:
            total = input_tokens + output_tokens
        if cached is not None and input_tokens is not None and cached > input_tokens:
            cached = None
        if reasoning is not None and output_tokens is not None and reasoning > output_tokens:
            reasoning = None
        return TokenUsage(input_tokens, output_tokens, total, cached, reasoning, self.final)
