"""Source-bound memory tracing without source text, lease tokens or dispatcher identity."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from functools import wraps
from typing import Any

from bridge.diagnostic_events import clean_fields, diagnostic_scope, event, new_request_id, scope_reference

_STATUS = {"complete": "succeeded", "deferred": "deferred", "stale_source": "rejected", "disabled": "skipped"}
_REASONS = frozenset(_STATUS) | {
    "work_failed",
    "configuration",
    "malformed_json",
    "invalid_shape",
    "invalid_audience",
    "invalid_npc_output",
    "retain_failed",
    "executor_rejected",
    "timeout",
    "network",
    "authentication",
    "credits",
    "provider_unavailable",
    "rate_limit",
    "model_unavailable",
}


def memory_identity(claim: Any) -> dict[str, object]:
    """The same durable source is linkable across attempts, but rewrites get a new identity."""
    revision = getattr(claim, "rewrite_identity", 0)
    source = json.dumps(
        [
            claim.chat_id,
            claim.session_id,
            claim.session_created_at,
            claim.layer,
            claim.version,
            claim.target_id,
            revision,
            getattr(claim, "purge_epoch", 0),
        ],
        separators=(",", ":"),
    )
    return clean_fields(
        {
            "request_id": "memory-" + scope_reference("memory", source),
            "chat_id": claim.chat_id,
            "session_id": claim.session_id,
            "layer": claim.layer,
            "purpose": claim.layer,
            "source_message_id": claim.target_id,
            "revision": revision,
            "operation_id": f"version-{claim.version}",
            "source": "durable_memory",
        }
    )


def observe_memory_claim(function: Callable[..., str]) -> Callable[..., str]:
    @wraps(function)
    def observed(db: Any, claim: Any, *args: Any, **kwargs: Any) -> str:
        with diagnostic_scope(inherit=False, **memory_identity(claim), worker_id=new_request_id("memory")):
            started = time.monotonic()
            status, reason = "failed", "work_failed"
            failure: dict[str, object] = {}
            event("memory.claim_start")
            try:
                result = function(db, claim, *args, **kwargs)
                status = _STATUS.get(result, "failed")
                reason = result if result in _REASONS else "work_failed"
                return result
            except BaseException as exc:
                failure["error_type"] = type(exc).__name__
                raise
            finally:
                event(
                    "memory.claim_finish",
                    status=status,
                    reason=reason,
                    elapsed_ms=max(0, int((time.monotonic() - started) * 1000)),
                    **failure,
                )

    return observed
