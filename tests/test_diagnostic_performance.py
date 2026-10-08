"""Template-only legacy formatting must not erase useful performance timings."""

import json
import logging

from bridge.diagnostic_events import diagnostic_scope
from bridge.diagnostic_logging import DiagnosticFormatter
from bridge.performance import _log_duration


def test_performance_span_survives_json_format_without_private_values(caplog):
    with caplog.at_level(logging.INFO), diagnostic_scope(request_id="tg-perf", job_id=9):
        _log_duration("prompt_build", 12.8, {"count": 4, "prompt": "private story", "actor": "private actor"})
    records = [
        record for record in caplog.records
        if getattr(record, "diagnostic_fields", {}).get("event") == "performance.span"
    ]
    assert len(records) == 1
    result = json.loads(DiagnosticFormatter().format(records[0]))
    assert result["request_id"] == "tg-perf"
    assert result["job_id"] == 9
    assert result["phase"] == "prompt_build"
    assert result["elapsed_ms"] == 12
    assert result["count"] == 4
    assert "private" not in json.dumps(result)


def test_clock_anomalies_do_not_break_optional_performance_logging(caplog):
    with caplog.at_level(logging.INFO):
        for duration in (-1.0, float("nan"), float("inf")):
            _log_duration("queue_wait", duration, {})
    values = [
        record.diagnostic_fields["elapsed_ms"] for record in caplog.records
        if getattr(record, "diagnostic_fields", {}).get("event") == "performance.span"
    ]
    assert len(values) == 3
    assert all(type(value) is int and 0 <= value <= 2**63 - 1 for value in values)
