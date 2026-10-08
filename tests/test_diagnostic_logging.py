"""Privacy and retention tests exercise real formatting and file rotation."""

import importlib.util
import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_diagnostic_logging_module_exists():
    assert importlib.util.find_spec("bridge.diagnostic_logging") is not None


def test_json_format_keeps_events_but_not_legacy_arguments():
    from bridge.diagnostic_logging import DiagnosticFormatter

    formatter = DiagnosticFormatter(["synthetic-private-credential"])
    record = logging.LogRecord(
        "bridge.test",
        logging.ERROR,
        str(Path(__file__).parents[1] / "bridge/provider_port.py"),
        1,
        "Provider failed: %s",
        ("private story",),
        None,
    )
    try:
        raise ValueError("synthetic-private-credential private story")
    except ValueError:
        import sys

        record.exc_info = sys.exc_info()
    data = json.loads(formatter.format(record))
    serialized = json.dumps(data)
    assert data["message"] == "Provider failed: %s"
    assert data["error_type"] == "ValueError"
    assert data["traceback"][-1]["function"] == "test_json_format_keeps_events_but_not_legacy_arguments"
    assert "private story" not in serialized
    assert "synthetic-private-credential" not in serialized
    assert str(Path(__file__).parent) not in serialized
    assert data["timestamp"].endswith("Z")


def test_formatter_redacts_tokens_and_rejects_extra_payloads():
    from bridge.diagnostic_logging import DiagnosticFormatter

    record = logging.LogRecord(
        "bridge.test", logging.INFO, __file__, 1, "Authorization: Bearer synthetic-private-credential", (), None
    )
    record.diagnostic_fields = {
        "event": "provider.finish",
        "model": "synthetic-private-credential",
        "prompt": "private story",
        "input_tokens": 8,
    }
    data = json.loads(DiagnosticFormatter(["synthetic-private-credential"]).format(record))
    assert "synthetic-private-credential" not in json.dumps(data)
    assert "private story" not in json.dumps(data)
    assert data["input_tokens"] == 8
    assert data["event"] == "provider.finish"


def test_formatter_bounds_legacy_message():
    from bridge.diagnostic_logging import DiagnosticFormatter

    record = logging.LogRecord("bridge.test", logging.INFO, __file__, 1, "x" * 100000, (), None)
    assert len(DiagnosticFormatter().format(record)) < 8192


def test_private_rotation_under_permissive_umask(tmp_path):
    from bridge.diagnostic_logging import DiagnosticFormatter, PrivateRotatingHandler

    previous = os.umask(0)
    handler = None
    try:
        handler = PrivateRotatingHandler(tmp_path / "runtime.log", maxBytes=250, backupCount=2)
        handler.setFormatter(DiagnosticFormatter())
        for _ in range(30):
            handler.handle(
                logging.LogRecord("bridge.test", logging.INFO, __file__, 1, "bounded diagnostic record", (), None)
            )
    finally:
        os.umask(previous)
        if handler:
            handler.close()
    paths = list(tmp_path.glob("runtime.log*"))
    assert len(paths) == 3
    for path in paths:
        assert path.stat().st_mode & 0o777 == 0o600
        for line in path.read_text().splitlines():
            assert json.loads(line)["schema"] == 1


def test_log_symlink_is_not_followed(tmp_path):
    from bridge.diagnostic_logging import PrivateRotatingHandler

    victim = tmp_path / "private.txt"
    victim.write_text("unchanged")
    link = tmp_path / "runtime.log"
    link.symlink_to(victim)
    with pytest.raises(OSError):
        PrivateRotatingHandler(link, maxBytes=1000, backupCount=1)
    assert victim.read_text() == "unchanged"


def test_disk_failure_is_counted_without_stderr_leak(tmp_path, monkeypatch, capsys):
    from bridge.diagnostic_logging import PrivateRotatingHandler

    handler = PrivateRotatingHandler(tmp_path / "runtime.log", maxBytes=1000, backupCount=1)
    try:

        def fail(record):
            raise OSError("private exception content")

        monkeypatch.setattr(handler, "shouldRollover", fail)
        handler.emit(logging.LogRecord("bridge.test", logging.INFO, __file__, 1, "private payload", (), None))
        assert handler.dropped == 1
        assert capsys.readouterr().err == ""
    finally:
        handler.close()


def test_logging_configuration_is_idempotent_and_json(tmp_path):
    from bridge.runtime_logging import configure_logging

    root = logging.getLogger()
    before = list(root.handlers)
    previous_level = root.level
    config = SimpleNamespace(log_file=tmp_path / "runtime.log", environ={}, bot_token="synthetic-bot-value")
    try:
        configure_logging(app_settings=config)
        configure_logging(app_settings=config)
        added = [h for h in root.handlers if h not in before]
        assert len(added) == 2
        assert len([h for h in added if getattr(h, "bridge_console", False)]) == 1
        from bridge.diagnostic_events import event

        event("diagnostic.test")
        assert json.loads(config.log_file.read_text().splitlines()[-1])["event"] == "diagnostic.test"
    finally:
        for handler in list(root.handlers):
            if handler not in before:
                root.removeHandler(handler)
                handler.close()
        root.setLevel(previous_level)


def test_rotation_options_are_bounded():
    from bridge.diagnostic_logging import logging_options

    assert logging_options({}) == (10 * 1024 * 1024, 5, logging.INFO)
    assert logging_options(
        {"SILLYTAVERN_LOG_MAX_MIB": "2", "SILLYTAVERN_LOG_BACKUPS": "1", "SILLYTAVERN_LOG_LEVEL": "WARNING"}
    ) == (2 * 1024 * 1024, 1, logging.WARNING)
    for values in [
        {"SILLYTAVERN_LOG_MAX_MIB": "0"},
        {"SILLYTAVERN_LOG_BACKUPS": "100"},
        {"SILLYTAVERN_LOG_LEVEL": "OFF"},
    ]:
        with pytest.raises(ValueError):
            logging_options(values)
