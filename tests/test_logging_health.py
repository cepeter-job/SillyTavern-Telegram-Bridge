"""Logging failures remain observable without storing records or performing log I/O."""

import io
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from bridge import diagnostic_events, runtime_logging
from bridge.diagnostic_logging import DiagnosticConsoleHandler, DiagnosticFormatter, PrivateRotatingHandler


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    root = logging.getLogger()
    previous_level = root.level
    monkeypatch.setattr(root, "handlers", [])
    monkeypatch.setattr(diagnostic_events, "_DROPPED", 0)
    monkeypatch.setattr(diagnostic_events, "_IDENTITY_KEY", b"fixture")
    config = SimpleNamespace(log_file=tmp_path / "private.log", environ={}, bot_token="synthetic-health-token")
    runtime_logging.configure_logging(app_settings=config)
    handlers = list(root.handlers)
    file_handler = next(h for h in handlers if isinstance(h, PrivateRotatingHandler))
    console = next(h for h in handlers if isinstance(h, DiagnosticConsoleHandler))
    console.setStream(io.StringIO())
    try:
        yield config, file_handler, console
    finally:
        for handler in handlers:
            root.removeHandler(handler)
            handler.close()
        root.setLevel(previous_level)


def unavailable(*args, **kwargs):
    raise OSError("PRIVATE_DISK_ERROR PRIVATE_STORY")


def test_health_reports_unconfigured_sinks_without_claiming_success(tmp_path, monkeypatch):
    monkeypatch.setattr(logging.getLogger(), "handlers", [])
    monkeypatch.setattr(diagnostic_events, "_DROPPED", 0)
    assert runtime_logging.logging_health(tmp_path / "unconfigured.log") == {
        "scope": "process",
        "file_configured": False,
        "console_configured": False,
        "event_failures_observed": False,
        "file_failures_observed": False,
        "console_failures_observed": False,
    }


def test_health_snapshot_is_read_only_and_content_free(runtime):
    config, _, console = runtime
    diagnostic_events.event("diagnostic.test")
    before = config.log_file.read_bytes(), console.stream.getvalue()
    for _ in range(5):
        health = runtime_logging.logging_health(runtime[0].log_file)
        assert health["file_configured"] is True
        assert health["console_configured"] is True
        assert health["event_failures_observed"] is False
        assert health["file_failures_observed"] is False
        assert health["console_failures_observed"] is False
    assert (config.log_file.read_bytes(), console.stream.getvalue()) == before
    for private in (str(config.log_file), config.bot_token, "diagnostic.test"):
        assert private not in json.dumps(health)


def test_file_failure_is_visible_after_success_without_disabling_console(runtime, monkeypatch):
    config, file_handler, console = runtime
    with monkeypatch.context() as patch:
        patch.setattr(file_handler, "shouldRollover", unavailable)
        diagnostic_events.event("diagnostic.failed_write")
    diagnostic_events.event("diagnostic.recovered_write")
    health = runtime_logging.logging_health(runtime[0].log_file)
    assert health["file_failures_observed"] is True
    assert health["console_failures_observed"] is False
    assert health["event_failures_observed"] is False
    assert len(console.stream.getvalue().splitlines()) == 2
    assert json.loads(config.log_file.read_text())["event"] == "diagnostic.recovered_write"
    assert "PRIVATE" not in json.dumps(health) + console.stream.getvalue()


def test_console_failure_does_not_hide_successful_file_write(runtime, monkeypatch):
    config, _, console = runtime
    monkeypatch.setattr(console.stream, "write", unavailable)
    diagnostic_events.event("diagnostic.console_failed")
    health = runtime_logging.logging_health(runtime[0].log_file)
    assert health["console_failures_observed"] is True
    assert health["file_failures_observed"] is False
    assert health["event_failures_observed"] is False
    assert json.loads(config.log_file.read_text())["event"] == "diagnostic.console_failed"


def test_event_failures_are_visible_under_concurrent_submission(runtime, monkeypatch):
    monkeypatch.setattr(logging.getLogger("bridge.events"), "log", unavailable)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: diagnostic_events.event("diagnostic.failure"), range(100)))
    health = runtime_logging.logging_health(runtime[0].log_file)
    assert diagnostic_events.diagnostic_failures() == 100
    assert health["event_failures_observed"] is True
    assert health["file_failures_observed"] is False
    assert health["console_failures_observed"] is False
    assert "PRIVATE" not in json.dumps(health)


@pytest.mark.parametrize("structured", [False, True])
def test_trusted_log_source_has_basename_function_and_line(structured):
    source = Path(__file__).parents[1] / "bridge/provider_port.py"
    record = logging.LogRecord(
        "root", logging.WARNING, str(source), 42, "Failure: %s", ("PRIVATE_REPLY",), None, func="complete"
    )
    if structured:
        record.diagnostic_fields = {"event": "provider.finish", "status": "failed"}
    data = json.loads(DiagnosticFormatter().format(record))
    assert (data["file"], data["function"], data["line"]) == ("provider_port.py", "complete", 42)
    assert str(source.parent) not in json.dumps(data)
    assert "PRIVATE_REPLY" not in json.dumps(data)


def test_external_log_source_details_remain_omitted():
    record = logging.LogRecord(
        "PRIVATE_LOGGER",
        logging.ERROR,
        "/private/PRIVATE_FILE.py",
        42,
        "PRIVATE_MESSAGE",
        (),
        None,
        func="PRIVATE_FUNCTION",
    )
    data = json.loads(DiagnosticFormatter().format(record))
    assert "file" not in data and "function" not in data
    assert "PRIVATE" not in json.dumps(data)


def test_event_log_record_identifies_the_call_site(caplog):
    with caplog.at_level(logging.INFO, logger="bridge.events"):
        diagnostic_events.event("diagnostic.caller")
    record = caplog.records[-1]
    assert record.pathname == __file__
    assert record.funcName == "test_event_log_record_identifies_the_call_site"
