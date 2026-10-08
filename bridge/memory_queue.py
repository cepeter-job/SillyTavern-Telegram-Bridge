"""Shared durable-memory readiness predicates and read-only backlog counters."""

import sqlite3
import time

AUTO_IDLE_SECONDS, AUTO_FAILURE_LIMIT = 86400, 8

CURRENT_SESSION = (
    "EXISTS(SELECT 1 FROM sessions s WHERE s.chat_id=memory_jobs.chat_id "
    "AND s.session_id=memory_jobs.session_id AND s.created_at=memory_jobs.session_created_at)"
)
RECENT_SOURCE = (
    "(layer='hindsight' OR EXISTS(SELECT 1 FROM messages m WHERE m.chat_id=memory_jobs.chat_id "
    "AND m.session_id=memory_jobs.session_id AND m.created_at>=:idle_since))"
)
RETRY_ALLOWED = (
    "(layer='hindsight' OR NOT (attempts>=:failure_limit AND last_error IN "
    "('work_failed','retain_failed','malformed_json','invalid_shape','invalid_audience','invalid_npc_output')))"
)
MODE_ENABLED = (
    "(layer NOT IN ('hindsight','curator') OR "
    "COALESCE((SELECT value FROM meta WHERE key='memory_mode:' || memory_jobs.chat_id),'on')='on')"
)
ELIGIBLE = (
    "dirty_version>completed_version AND (lease_token='' OR lease_deadline<=:now) AND next_attempt_at<=:now "
    f"AND {CURRENT_SESSION} AND (:autonomous=0 OR ({RECENT_SOURCE} AND {RETRY_ALLOWED} AND {MODE_ENABLED}))"
)
CLAIM_SELECTION = (
    "SELECT chat_id,session_id,session_created_at,layer,dirty_version,target_id FROM memory_jobs "  # noqa: S608 -- fixed readiness SQL, bound values
    f"WHERE {ELIGIBLE} ORDER BY next_attempt_at,attempts,chat_id,session_id,layer"
)
QUEUE_CLASSIFICATION = (
    "SELECT category,COUNT(*),MIN(pending_since),SUM(pending_since IS NULL) FROM (SELECT pending_since,CASE "  # noqa: S608 -- fixed readiness SQL, bound values
    "WHEN lease_token<>'' AND lease_deadline>:now THEN 'leased' "
    f"WHEN NOT {CURRENT_SESSION} THEN 'inactive' WHEN NOT {MODE_ENABLED} THEN 'disabled' "
    f"WHEN NOT {RECENT_SOURCE} THEN 'inactive' WHEN NOT {RETRY_ALLOWED} THEN 'parked' "
    "WHEN next_attempt_at>:now THEN 'backoff' ELSE 'eligible' END AS category "
    "FROM memory_jobs WHERE dirty_version>completed_version) GROUP BY category"
)


def queue_parameters(now: float, *, autonomous: bool = True) -> dict[str, float | int]:
    return {
        "now": now,
        "idle_since": now - AUTO_IDLE_SECONDS,
        "failure_limit": AUTO_FAILURE_LIMIT,
        "autonomous": int(autonomous),
    }


def queue_counters(db: sqlite3.Connection, *, now: float | None = None) -> dict[str, int]:
    """Count readiness, excluding executor capacity; never recover leases or mutate the queue."""
    now = time.time() if now is None else now
    counts = {name: 0 for name in ("eligible", "leased", "backoff", "inactive", "parked", "disabled")}
    oldest, unknown = None, 0
    for category, count, pending_since, missing in db.execute(QUEUE_CLASSIFICATION, queue_parameters(now)).fetchall():
        counts[category] = int(count)
        if category == "eligible":
            oldest, unknown = pending_since, int(missing)
    result = {f"memory.jobs.{name}": count for name, count in counts.items()}
    result["memory.jobs.pending"] = sum(counts.values())
    result["memory.jobs.eligible_age_unknown"] = unknown
    result["memory.jobs.oldest_eligible_age_ms"] = (
        -1 if unknown else max(0, round((now - oldest) * 1000)) if oldest is not None else 0
    )
    return result
