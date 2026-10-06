"""Pure application port for model-provider generation."""

from __future__ import annotations

import logging
import socket
import threading
import time
import urllib.error
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from functools import partial

from bridge.port_contracts import CancellationEvent, ProviderGenerate, ProviderPolicy
from bridge.provider_errors import (
    ProviderRequestError,
    ProviderTransportError,
    parse_retry_after,
    provider_category_for_status,
)
from bridge.token_usage_values import TokenUsage, UsageEvent, UsageRecorder, UsageScope

_PROVIDER_ACTIVITY_LOCK = threading.Lock()
_PROVIDER_ACTIVITY: dict[int, tuple[str, str, str, float]] = {}
_PROVIDER_ACTIVITY_NEXT = 0


def _provider_parts(selection: str) -> tuple[str, str]:
    provider, separator, model = str(selection or "").partition("::")
    if not separator:
        return "unqualified", str(selection or "")[:160]
    return provider[:80], model[:160]


def _start_provider_activity(selection: str, purpose: str) -> tuple[int, str, str]:
    global _PROVIDER_ACTIVITY_NEXT
    provider, model = _provider_parts(selection)
    with _PROVIDER_ACTIVITY_LOCK:
        _PROVIDER_ACTIVITY_NEXT += 1
        token = _PROVIDER_ACTIVITY_NEXT
        _PROVIDER_ACTIVITY[token] = (provider, model, str(purpose or "")[:64], time.monotonic())
    return token, provider, model


def _finish_provider_activity(token: int) -> None:
    with _PROVIDER_ACTIVITY_LOCK:
        _PROVIDER_ACTIVITY.pop(token, None)


def active_provider_requests() -> tuple[dict[str, object], ...]:
    now = time.monotonic()
    with _PROVIDER_ACTIVITY_LOCK:
        active = tuple(sorted(_PROVIDER_ACTIVITY.items()))[:8]
    return tuple(
        {
            "provider": provider,
            "model": model,
            "purpose": purpose,
            "elapsed_ms": max(0, int((now - started) * 1000)),
        }
        for _token, (provider, model, purpose, started) in active
    )


def normalize_provider_exception(error: BaseException, model: str) -> ProviderRequestError | None:
    """Convert provider transport exceptions into the canonical sanitized error."""
    if isinstance(error, ProviderRequestError):
        return error
    if isinstance(error, ProviderTransportError):
        return ProviderRequestError(model, error.category, error.status, retry_after=error.retry_after)
    if isinstance(error, urllib.error.HTTPError):
        try:
            status = int(error.code)
        except (TypeError, ValueError):
            return None
        headers = error.headers
        delay = parse_retry_after(headers.get("Retry-After")) if headers is not None else None
        return ProviderRequestError(model, provider_category_for_status(status), status, retry_after=delay)
    if isinstance(error, (TimeoutError, socket.timeout)):
        return ProviderRequestError(model, "timeout")
    if isinstance(error, urllib.error.URLError):
        reason = getattr(error, "reason", None)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return ProviderRequestError(model, "timeout")
        return ProviderRequestError(model, "network")
    if isinstance(error, (ConnectionError, OSError)):
        return ProviderRequestError(model, "network")
    return None


@dataclass(frozen=True)
class ProviderPort:
    generate_backend: ProviderGenerate
    usage_recorder: UsageRecorder | None = None
    usage_scope: UsageScope | None = None
    policy: ProviderPolicy | None = None
    context_observer: Callable[[dict[str, object]], None] | None = None

    def with_context_observer(self, observer: Callable[[dict[str, object]], None]) -> ProviderPort:
        """Bind a pure observer for this one call, including fallback attempts."""
        return replace(self, context_observer=observer)

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
        purpose = self.usage_scope.purpose if self.usage_scope is not None else ""
        candidates = self.policy.candidates(model, purpose) if self.policy is not None else (model,)
        visible = False

        def report(text: str) -> object:
            nonlocal visible
            visible = visible or bool(text)
            return stream_callback(text) if stream_callback is not None else None

        started = time.monotonic()
        budget = request_timeout if request_timeout is not None else 240.0
        for index, candidate in enumerate(candidates):
            if self.policy is not None and cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("Provider request cancelled")
            timeout = request_timeout if index == 0 else max(0.001, budget - (time.monotonic() - started))
            try:
                return self._generate_once(
                    api_key if index == 0 else "",
                    candidate,
                    messages,
                    session_id=session_id,
                    settings=settings,
                    stream_callback=report
                    if self.policy is not None and stream_callback is not None
                    else stream_callback,
                    cancel_event=cancel_event,
                    force_non_stream=force_non_stream,
                    request_timeout=timeout,
                )
            except ProviderRequestError as error:
                if (
                    index + 1 == len(candidates)
                    or error.category
                    not in {
                        "timeout",
                        "network",
                        "provider_unavailable",
                        "rate_limit",
                        "authentication",
                        "credits",
                        "model_unavailable",
                    }
                    or visible
                    or (cancel_event is not None and cancel_event.is_set())
                    or time.monotonic() - started >= budget
                ):
                    raise
        raise RuntimeError("No provider route available")  # defensive contract guard

    def _generate_once(
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
        if self.context_observer is not None:
            backend = partial(backend, context_observer=self.context_observer)
        tracking = self.usage_recorder is not None and self.usage_scope is not None
        if tracking:
            backend = partial(backend, usage_callback=readings.append)
        attempt = self.policy.begin(model) if self.policy is not None else None
        observed_model = attempt.selection if attempt is not None else model
        purpose = self.usage_scope.purpose if self.usage_scope is not None else ""
        activity_token: int | None = None
        observed_provider, observed_model_id = _provider_parts(observed_model)
        callback_failed = False

        def observe_stream(text: str) -> object:
            nonlocal callback_failed
            try:
                return stream_callback(text) if stream_callback is not None else None
            except Exception:
                callback_failed = True
                raise

        started = time.monotonic()
        status = "failed"
        try:
            activity_token, observed_provider, observed_model_id = _start_provider_activity(observed_model, purpose)
            logging.info(
                "provider_start purpose=%s provider=%s model=%s",
                purpose,
                observed_provider,
                observed_model_id,
            )
            result = str(
                backend(
                    api_key,
                    model,
                    messages,
                    session_id=session_id,
                    settings=settings,
                    stream_callback=(
                        observe_stream if self.policy is not None and stream_callback is not None else stream_callback
                    ),
                    cancel_event=cancel_event,
                    force_non_stream=force_non_stream,
                    request_timeout=request_timeout,
                )
            )
            cancelled = (tracking or self.policy is not None) and cancel_event is not None and cancel_event.is_set()
            status = "cancelled" if cancelled else "succeeded"
            if self.policy is not None and attempt is not None:
                if cancelled:
                    self.policy.cancel(attempt)
                else:
                    self.policy.succeed(attempt)
            return result
        except Exception as exc:
            if (tracking or self.policy is not None) and cancel_event is not None and cancel_event.is_set():
                status = "cancelled"
            if callback_failed:
                raise
            normalized = normalize_provider_exception(exc, observed_model)
            if self.policy is not None and attempt is not None and normalized is not None:
                if cancel_event is None or not cancel_event.is_set():
                    self.policy.fail(attempt, normalized)
            if normalized is not None:
                raise normalized from None
            raise
        finally:
            elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
            if activity_token is not None:
                _finish_provider_activity(activity_token)
            logging.info(
                "provider_finish purpose=%s provider=%s model=%s status=%s elapsed_ms=%s",
                purpose,
                observed_provider,
                observed_model_id,
                status,
                elapsed_ms,
            )
            if self.policy is not None and attempt is not None:
                self.policy.cancel(attempt)
            if tracking and self.usage_recorder is not None and self.usage_scope is not None:
                event = UsageEvent(
                    self.usage_scope,
                    observed_model,
                    tuple(readings),
                    elapsed_ms,
                    status,
                )
                try:
                    self.usage_recorder(event)
                except Exception:
                    logging.warning("Token usage could not be recorded; response delivery is unchanged")
