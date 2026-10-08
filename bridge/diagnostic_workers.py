"""Preserve only diagnostic identity across thread and ordered-queue boundaries."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import ParamSpec, TypeVar

from bridge.diagnostic_events import bind_diagnostics, clean_fields, diagnostic_context, diagnostic_scope

P = ParamSpec("P")
T = TypeVar("T")
_ATTRIBUTE = "_bridge_diagnostic_identity"


def worker_identity(function: Callable[..., object]) -> dict[str, object]:
    captured = getattr(function, _ATTRIBUTE, None)
    return clean_fields(captured) if isinstance(captured, dict) else diagnostic_context()


def bind_worker(function: Callable[P, T], *, identity: Mapping[str, object] | None = None) -> Callable[P, T]:
    """Capture enqueue-time scalars, preserving a durable worker's trusted scope."""
    captured = worker_identity(function) if identity is None else clean_fields(identity)
    with diagnostic_scope(inherit=False, **captured):
        bound = bind_diagnostics(function)
    setattr(bound, _ATTRIBUTE, captured)
    return bound
