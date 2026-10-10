"""Durable-memory retry classifications shared by the worker store."""

MEMORY_RESPONSE_ERRORS = {
    "episodic memory response is not valid JSON": "malformed_json",
    "episodic memory response must be a JSON array": "invalid_shape",
    "episodic memory empty result requires a reason": "invalid_shape",
    "episodic memory populated result has no accepted candidates": "invalid_shape",
    "Classified memory response must be a JSON object": "invalid_shape",
    "Memory classification requires at most 32 explicit blocks": "invalid_shape",
    "Classified memory blocks require text": "invalid_shape",
    "Classified memory text must be bounded and nonempty": "invalid_shape",
    "Classified memory exceeds the artifact bound": "invalid_shape",
    "Classified summary requires bounded, nonempty output": "invalid_shape",
    "Scene extraction requires an explicit state object": "invalid_shape",
    "Scene extraction requires valid state": "invalid_shape",
    "Memory classification must explicitly name shared or restricted visibility": "invalid_audience",
    "Memory audience must contain character names": "invalid_audience",
    "Restricted memory requires an identified character": "invalid_audience",
    "Shared memory cannot carry a conflicting restricted audience": "invalid_audience",
    "NPC extractor returned malformed output": "invalid_npc_output",
    "NPC extractor returned malformed NPC output": "invalid_npc_output",
}
RESPONSE_FAILURE_CODES = frozenset(MEMORY_RESPONSE_ERRORS.values())

MEMORY_FAILURE_CODES = RESPONSE_FAILURE_CODES | frozenset(
    {
        "work_failed",
        "rate_limit",
        "retain_failed",
        "stale_source",
        "executor_rejected",
        "disabled",
        "deferred",
        "configuration",
    }
)
CONFIGURATION_RETRY_SECONDS = 3600
