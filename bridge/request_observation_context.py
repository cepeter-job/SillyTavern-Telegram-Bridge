"""Explicitly scoped, failure-isolated hooks for one physical generation request.

No configuration, storage, networking, or provider dependency belongs here.
Bindings are reset on every exit, including nested continuations and retries.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Protocol, TypeVar

from bridge.diagnostic_events import diagnostic_context


class ObservationTicket(Protocol):
    def usage(self, reading: object) -> None: ...

    def finish(self, *, failed: bool, elapsed_ms: int) -> None: ...


class RequestObserver(Protocol):
    def profile_identity(self, messages: list[dict], session: dict) -> dict[str, str] | None: ...

    def begin(
        self,
        body: dict,
        messages: list[dict],
        *,
        scope: object,
        identity: dict[str, str] | None,
        selection: str,
        route: str,
        transport: str,
        phase: str,
        request_bytes: int,
    ) -> ObservationTicket | None: ...


_BINDING: ContextVar[tuple[RequestObserver, object, dict[str, str] | None, object] | None] = ContextVar(
    "sttb_request_observation_binding", default=None
)
_TICKET: ContextVar[ObservationTicket | None] = ContextVar("sttb_physical_request_observation", default=None)


@contextmanager
def logical_observation(
    observer: RequestObserver | None,
    scope: object,
    identity: dict[str, str] | None,
) -> Iterator[None]:
    # Always mask an outer binding when a nested port did not opt in.
    token = _BINDING.set(
        (observer, scope, identity, diagnostic_context().get("call_id")) if observer is not None else None
    )
    try:
        yield
    finally:
        _BINDING.reset(token)


@contextmanager
def observed_request(
    body: dict,
    messages: list[dict],
    *,
    selection: str,
    route: str,
    transport: str,
    phase: str,
    request_bytes: int,
) -> Iterator[None]:
    binding = _BINDING.get()
    ticket = None
    if binding is not None and binding[3] == diagnostic_context().get("call_id"):
        observer, scope, identity, _call = binding
        try:
            ticket = observer.begin(
                body,
                messages,
                scope=scope,
                identity=identity,
                selection=selection,
                route=route,
                transport=transport,
                phase=phase,
                request_bytes=request_bytes,
            )
        except Exception:  # noqa: S110 -- diagnostics must not affect request outcome or leak an exception
            # No exception/message/traceback retained or logged by the observer.
            pass
    token = _TICKET.set(ticket)
    started = time.monotonic()
    failed = True
    try:
        yield
        failed = False
    finally:
        _TICKET.reset(token)
        if ticket is not None:
            try:
                ticket.finish(failed=failed, elapsed_ms=max(0, int((time.monotonic() - started) * 1000)))
            except Exception:  # noqa: S110 -- diagnostics must not affect request outcome or leak an exception
                pass


def note_observed_usage(reading: object) -> None:
    ticket = _TICKET.get()
    if ticket is not None:
        try:
            ticket.usage(reading)
        except Exception:  # noqa: S110 -- diagnostics must not affect request outcome or leak an exception
            pass


def bind_observation(observer: RequestObserver, scope: object, identity: dict[str, str] | None) -> Callable[[], None]:
    binding_token = _BINDING.set((observer, scope, identity, diagnostic_context().get("call_id")))
    ticket_token = _TICKET.set(None)

    def reset() -> None:
        _TICKET.reset(ticket_token)
        _BINDING.reset(binding_token)

    return reset


Reading = TypeVar("Reading")


def observed_usage_callback(callback: Callable[[Reading], None] | None) -> Callable[[Reading], None] | None:
    """Bind usage to this send, preserving the application's original callback."""
    ticket = _TICKET.get()
    if ticket is None:
        return callback

    def combined(reading: Reading) -> None:
        try:
            ticket.usage(reading)
        except Exception:  # noqa: S110 -- observation must not change usage delivery
            pass
        if callback is not None:
            callback(reading)

    return combined
