"""Durable-memory retry classifications shared by the worker store."""

from bridge.memory_response import RESPONSE_FAILURE_CODES

MEMORY_FAILURE_CODES = RESPONSE_FAILURE_CODES | frozenset(
    {
        "work_failed",
        "retain_failed",
        "stale_source",
        "executor_rejected",
        "disabled",
        "deferred",
        "configuration",
    }
)
CONFIGURATION_RETRY_SECONDS = 3600
