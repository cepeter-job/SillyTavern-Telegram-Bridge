"""Scalar-only observations of synchronous application boundaries."""

from __future__ import annotations

import inspect
import logging
import time
from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, TypeVar

from bridge.diagnostic_events import clean_fields, diagnostic_context, diagnostic_scope, event, new_request_id

P = ParamSpec("P")
T = TypeVar("T")


def observe_boundary(
    name: str, *, decision: bool = False, success: str = "succeeded"
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Only named identity scalars enter context; argument/result objects are never retained."""

    def decorate(function: Callable[P, T]) -> Callable[P, T]:
        positions = {
            key: index
            for index, (key, parameter) in enumerate(inspect.signature(function).parameters.items())
            if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
        }

        @wraps(function)
        def observed(*args: P.args, **kwargs: P.kwargs) -> T:
            def value(key: str) -> object:
                index = positions.get(key)
                return (
                    kwargs.get(key)
                    if key in kwargs
                    else args[index]
                    if index is not None and index < len(args)
                    else None
                )

            session = value("session")
            session_id = value("session_id")
            if session_id is None and isinstance(session, dict):
                session_id = session.get("session_id")
            identity = clean_fields(
                {
                    "chat_id": value("chat_id"),
                    "session_id": session_id,
                    "source_message_id": value("assistant_rowid"),
                }
            )
            parent = diagnostic_context()
            matching = all(
                key not in parent or parent[key] == item
                for key, item in identity.items()
                if key in {"chat_ref", "session_ref"}
            )
            identity["request_id"] = (
                parent.get("request_id") if matching and parent.get("request_id") else new_request_id("operation")
            )
            with diagnostic_scope(inherit=matching, **identity):
                started = time.monotonic()
                status = "failed"
                outcome: dict[str, object] = {}
                event(name + "_start")
                try:
                    result = function(*args, **kwargs)
                    status = success
                    if decision:
                        candidate = getattr(result, "result", None)
                        status = (
                            candidate
                            if isinstance(candidate, str) and candidate in {"accepted", "rejected"}
                            else "unknown"
                        )
                        outcome = clean_fields({"revision": getattr(result, "expected_revision", None)})
                    return result
                except BaseException as exc:
                    outcome = {"error_type": type(exc).__name__}
                    raise
                finally:
                    event(
                        name + "_finish",
                        level=logging.INFO,
                        exc_info="error_type" in outcome,
                        status=status,
                        elapsed_ms=max(0, int((time.monotonic() - started) * 1000)),
                        **outcome,
                    )

        return observed

    return decorate
