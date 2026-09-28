"""Pure application port for model-provider generation."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from functools import partial

from bridge.port_contracts import CancellationEvent, ProviderGenerate
from bridge.token_usage_values import TokenUsage, UsageEvent, UsageRecorder, UsageScope


@dataclass(frozen=True)
class ProviderPort:
    generate_backend: ProviderGenerate
    usage_recorder: UsageRecorder | None = None
    usage_scope: UsageScope | None = None

    def for_usage(self, chat_id: str, session_id: str, purpose: str) -> ProviderPort:
        """Bind trusted use-case identity; never infer ownership from provider session strings."""
        return replace(self, usage_scope=UsageScope(str(chat_id), str(session_id), purpose))

    def for_purpose(self, purpose: str) -> ProviderPort:
        if self.usage_scope is None:
            return self
        return replace(self, usage_scope=replace(self.usage_scope, purpose=purpose))

    def generate(
        self,
        api_key: str,
        model: str,
        messages: list[dict],
        *,
        session_id: str = "telegram",
        settings: Mapping[str, object] | None = None,
        stream_callback: Callable[[str], object] | None = None,
        cancel_event: CancellationEvent | None = None,
        force_non_stream: bool = False,
        request_timeout: float | None = None,
    ) -> str:
        readings: list[TokenUsage] = []
        backend = self.generate_backend
        tracking = self.usage_recorder is not None and self.usage_scope is not None
        if tracking:
            backend = partial(backend, usage_callback=readings.append)
        started = time.monotonic()
        status = "failed"
        try:
            result = str(
                backend(
                    api_key,
                    model,
                    messages,
                    session_id=session_id,
                    settings=settings,
                    stream_callback=stream_callback,
                    cancel_event=cancel_event,
                    force_non_stream=force_non_stream,
                    request_timeout=request_timeout,
                )
            )
            status = "cancelled" if tracking and cancel_event is not None and cancel_event.is_set() else "succeeded"
            return result
        finally:
            if tracking and self.usage_recorder is not None and self.usage_scope is not None:
                event = UsageEvent(
                    self.usage_scope, model, tuple(readings), max(0, int((time.monotonic() - started) * 1000)), status
                )
                try:
                    self.usage_recorder(event)
                except Exception:
                    logging.warning("Token usage could not be recorded; response delivery is unchanged")
