"""Telegram transport budgets and content-free request diagnostics."""

from __future__ import annotations

import io
import json
import logging
import urllib.error

import pytest

from bridge import telegram
from bridge.diagnostic_logging import DiagnosticFormatter


@pytest.mark.parametrize(
    "method,expected_timeout",
    [("answerCallbackQuery", 5), ("editMessageReplyMarkup", 5), ("getUpdates", 65), ("sendMessage", 65)],
)
def test_callback_methods_have_short_socket_budgets(monkeypatch, method, expected_timeout):
    observed = []

    def open_response(_request, *, timeout):
        observed.append(timeout)
        return io.BytesIO(b'{"ok":true,"result":{}}')

    monkeypatch.setattr(telegram.urllib.request, "urlopen", open_response)
    assert telegram.telegram_request("fixture", method, {}) == {}
    assert observed == [expected_timeout]


def _events(caplog):
    return [
        record
        for record in caplog.records
        if getattr(record, "diagnostic_fields", {}).get("event") == "telegram.request_finish"
    ]


@pytest.mark.parametrize(
    "method,logged_method", [("answerCallbackQuery", "answerCallbackQuery"), ("private-method", "other")]
)
def test_http_failure_diagnostics_keep_only_bounded_fields(monkeypatch, caplog, method, logged_method):
    private = "synthetic-private-token-and-response"
    body = io.BytesIO(json.dumps({"ok": False, "description": private}).encode())
    error = urllib.error.HTTPError(f"https://api.telegram.org/{private}", 429, private, {}, body)

    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(telegram.urllib.request, "urlopen", fail)
    caplog.set_level(logging.INFO, logger="bridge.events")
    try:
        with pytest.raises(RuntimeError):
            telegram.telegram_request(private, method, {"text": private})
        records = _events(caplog)
        assert len(records) == 1
        fields = records[0].diagnostic_fields
        assert fields["method"] == logged_method
        assert fields["http_status"] == 429
        assert fields["status"] == "failed"
        assert fields["reason"] == "http_error"
        assert type(fields["elapsed_ms"]) is int and fields["elapsed_ms"] >= 0
        encoded = DiagnosticFormatter().format(records[0])
        assert private not in encoded
        assert "https://" not in encoded
        assert "description" not in encoded
        assert body.closed
    finally:
        error.close()


@pytest.mark.parametrize(
    "error,reason",
    [(TimeoutError("private-timeout"), "timeout"), (urllib.error.URLError("private-network"), "transport_error")],
)
def test_transport_failures_keep_original_exception_and_safe_diagnostics(monkeypatch, caplog, error, reason):
    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(telegram.urllib.request, "urlopen", fail)
    caplog.set_level(logging.INFO, logger="bridge.events")
    with pytest.raises(type(error)) as caught:
        telegram.telegram_request("fixture", "getUpdates", {})
    assert caught.value is error
    fields = _events(caplog)[0].diagnostic_fields
    assert fields["method"] == "getUpdates"
    assert fields["status"] == "failed"
    assert fields["reason"] == reason


def test_success_diagnostic_reports_method_status_and_elapsed(monkeypatch, caplog):
    monkeypatch.setattr(telegram.urllib.request, "urlopen", lambda *_a, **_k: io.BytesIO(b'{"ok":true,"result":[]}'))
    caplog.set_level(logging.INFO, logger="bridge.events")
    assert telegram.telegram_request("fixture", "getUpdates", {}) == []
    fields = _events(caplog)[0].diagnostic_fields
    assert fields["method"] == "getUpdates"
    assert fields["status"] == "succeeded"
    assert fields["reason"] == "ok"
    assert fields["elapsed_ms"] >= 0


def test_application_failure_is_not_reclassified_as_transport_error(monkeypatch):
    def fail(*_args, **_kwargs):
        raise RuntimeError("unexpected application bug")

    monkeypatch.setattr(telegram.urllib.request, "urlopen", fail)
    with pytest.raises(RuntimeError, match="unexpected application bug"):
        telegram.telegram_request("fixture", "answerCallbackQuery", {})
