"""Read-only, content-free summary-recovery readiness snapshot.

Counts are not a durable lease or permission to run helper models. The
snapshot intentionally never reports chat/session IDs, source text, or
provider requests.
"""

from __future__ import annotations

import sqlite3
import time

from bridge.memory_queue import (
    CURRENT_SESSION,
    MODE_ENABLED,
    RECENT_SOURCE,
    RETRY_ALLOWED,
    queue_parameters,
)
from bridge.memory_retry import MEMORY_FAILURE_CODES

_SUMMARY_QUEUE_CATEGORIES = (
    "SELECT category,COUNT(*) FROM (SELECT CASE "  # noqa: S608 -- fixed SQL over internal tables
    "WHEN lease_token<>'' AND lease_deadline>:now THEN 'leased' "
    f"WHEN NOT {CURRENT_SESSION} THEN 'inactive' WHEN NOT {MODE_ENABLED} THEN 'disabled' "
    f"WHEN NOT {RECENT_SOURCE} THEN 'inactive' WHEN NOT {RETRY_ALLOWED} THEN 'parked' "
    "WHEN next_attempt_at>:now THEN 'backoff' ELSE 'eligible' END AS category "
    "FROM memory_jobs WHERE layer='summary' AND dirty_version>completed_version) GROUP BY category"
)

_MANUAL_DUE = (
    "SELECT COUNT(*) FROM memory_jobs WHERE layer='summary' "  # noqa: S608 -- fixed internal SQL using audited queue predicates
    "AND dirty_version>completed_version AND (lease_token='' OR lease_deadline<=:now) "
    f"AND next_attempt_at<=:now AND {CURRENT_SESSION}"
)


def summary_recovery_snapshot(db: sqlite3.Connection, *, now: float | None = None) -> dict[str, object]:
    """Classify pending summary work without consuming leases or source contents."""
    if now is None:
        now = time.time()
    params = queue_parameters(now)
    jobs = {name: 0 for name in ("eligible", "leased", "backoff", "inactive", "parked", "disabled")}
    for category, count in db.execute(_SUMMARY_QUEUE_CATEGORIES, params):
        if category not in jobs:
            raise ValueError("Unknown durable summary queue category")
        jobs[category] = int(count)
    jobs["pending"] = sum(jobs.values())
    jobs["manual_due"] = int(db.execute(_MANUAL_DUE, params).fetchone()[0])
    jobs["max_attempts"] = int(
        db.execute(
            "SELECT COALESCE(MAX(attempts),0) FROM memory_jobs "
            "WHERE layer='summary' AND dirty_version>completed_version"
        ).fetchone()[0]
    )
    error_codes: dict[str, int] = {}
    for code, count in db.execute(
        "SELECT last_error,COUNT(*) FROM memory_jobs WHERE layer='summary' "
        "AND dirty_version>completed_version AND last_error<>'' GROUP BY last_error"
    ):
        safe = str(code) if code in MEMORY_FAILURE_CODES else "other"
        error_codes[safe] = error_codes.get(safe, 0) + int(count)
    jobs["error_codes"] = error_codes

    # Only currently owned incarnations matter; deleted/old session rows are
    # excluded, rather than silently treated as a valid current checkpoint.
    total, invalidated, lagging = db.execute(
        "SELECT COUNT(*),"
        "COALESCE(SUM(CASE WHEN l.invalidated_from_id IS NOT NULL THEN 1 ELSE 0 END),0),"
        "COALESCE(SUM(CASE WHEN EXISTS(SELECT 1 FROM messages m "
        "WHERE m.chat_id=l.chat_id AND m.session_id=l.session_id AND m.id>l.covered_id) "
        "THEN 1 ELSE 0 END),0) "
        "FROM memory_layer_state l JOIN sessions s ON s.chat_id=l.chat_id "
        "AND s.session_id=l.session_id AND s.created_at=l.session_created_at "
        "WHERE l.layer='summary'"
    ).fetchone()
    return {
        "schema_version": 1,
        "source": "existing_sqlite_read_only",
        "summary": {
            "layers": {"total": int(total), "invalidated": int(invalidated), "covered_lagging": int(lagging)},
            "jobs": jobs,
            "catchup_complete": bool(total and not invalidated and not lagging and not jobs["pending"]),
        },
        "approval_ready": False,
        "meaning": "Queue snapshot only; accepted summary coverage and causal review remain separate requirements.",
        "blocking_reasons": [
            "live_summary_recovery_not_verified",
            "native_required_fact_and_causal_proof_required",
            "provider_reported_input_and_blinded_review_required",
        ],
    }
