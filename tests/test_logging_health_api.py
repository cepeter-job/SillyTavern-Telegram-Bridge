"""Session exports warn about lost capture without disclosing process-wide counts."""

import io
import json
import logging

import pytest
from miniapp_test_support import identity, make_services

from bridge import diagnostic_events
from bridge.diagnostic_logging import DiagnosticConsoleHandler, PrivateRotatingHandler
from bridge.miniapp_context import current_session
from bridge.miniapp_diagnostics import diagnostic_export
from bridge.runtime_logging import configure_logging


@pytest.mark.parametrize(
    "failure", ["none", "file", "console", "event", "missing_file", "wrong_file", "other_file_failure"]
)
def test_known_capture_failure_invalidates_usage_without_exporting_counts(tmp_path, monkeypatch, failure):
    root = logging.getLogger()
    previous_level = root.level
    monkeypatch.setattr(root, "handlers", [])
    monkeypatch.setattr(diagnostic_events, "_DROPPED", 0)
    monkeypatch.setattr(diagnostic_events, "_IDENTITY_KEY", b"fixture")
    services = make_services(tmp_path)
    who = identity()
    session = current_session(services, who, {})["session"]
    configure_logging(app_settings=services.config)
    handlers = list(root.handlers)
    file_handler = next(h for h in handlers if isinstance(h, PrivateRotatingHandler))
    console = next(h for h in handlers if isinstance(h, DiagnosticConsoleHandler))
    console.setStream(io.StringIO())

    def broken(*args, **kwargs):
        raise OSError("PRIVATE_WRITE_ERROR")

    try:
        with diagnostic_events.diagnostic_scope(
            inherit=False, chat_id=who.chat_id, session_id=session["session_id"], request_id="retained-request"
        ):
            diagnostic_events.event("provider.start", call_id="call-retained", attempt=1, purpose="story")
            diagnostic_events.event(
                "provider.finish",
                call_id="call-retained",
                attempt=1,
                purpose="story",
                status="succeeded",
                usage_reported=True,
                usage_complete=True,
                input_tokens=12,
                output_tokens=8,
            )
        if failure == "file":
            monkeypatch.setattr(file_handler, "shouldRollover", broken)
        elif failure == "console":
            monkeypatch.setattr(console.stream, "write", broken)
        elif failure == "event":
            monkeypatch.setattr(logging.getLogger("bridge.events"), "log", broken)
        elif failure == "missing_file":
            root.removeHandler(file_handler)
        elif failure in {"wrong_file", "other_file_failure"}:
            configure_logging(tmp_path / "unrelated.log", app_settings=services.config)
            other_handler = next(handler for handler in root.handlers if handler not in handlers)
            handlers.append(other_handler)
            if failure == "wrong_file":
                root.removeHandler(file_handler)
            else:
                monkeypatch.setattr(other_handler, "shouldRollover", broken)
        # Process-wide failures must not expose another session's activity counts or events.
        with diagnostic_events.diagnostic_scope(inherit=False):
            diagnostic_events.event("provider.start", call_id="unscoped-call", attempt=1)
            diagnostic_events.event("provider.finish", call_id="unscoped-call", attempt=1)
        result = diagnostic_export(services, who, {"session_id": session["session_id"]})
        health = result["runtime"]["logging"]
        assert health["scope"] == "process"
        assert health["file_configured"] is (failure not in {"missing_file", "wrong_file"})
        assert health["console_configured"] is True
        assert health["file_failures_observed"] is (failure == "file")
        assert health["console_failures_observed"] is (failure == "console")
        assert health["event_failures_observed"] is (failure == "event")
        assert all(type(value) is bool for key, value in health.items() if key != "scope")
        expected = failure in {"none", "console", "other_file_failure"}
        assert result["usage"]["complete"] is expected
        assert result["usage_by_purpose"][0]["complete"] is expected
        assert result["usage"]["input_tokens"] == 12
        provider_events = [item for item in result["events"] if item["event"].startswith("provider.")]
        assert len(provider_events) == 2
        assert {item["call_id"] for item in provider_events} == {"call-retained"}
        assert "unscoped-call" not in json.dumps(result)
        assert "PRIVATE" not in json.dumps(result)
        assert str(services.config.log_file) not in json.dumps(result)
    finally:
        for handler in handlers:
            root.removeHandler(handler)
            handler.close()
        root.setLevel(previous_level)
