"""Preformatted third-party/root records must not disclose story content."""

import json
import logging

from bridge.diagnostic_logging import DiagnosticFormatter


def test_third_party_preformatted_messages_are_omitted():
    text = "User: PRIVATE_USER_INPUT; assistant: PRIVATE_STORY_REPLY; plan: PRIVATE_DIRECTOR_PLAN"
    for logger in ("vendor.transport", "httpx", "root", "PRIVATE_LOGGER_NAME"):
        record = logging.LogRecord(
            logger, logging.DEBUG, "/vendor/client.py", 23, f"request completed: {text}", (), None
        )
        result = json.loads(DiagnosticFormatter().format(record))
        assert "PRIVATE_" not in json.dumps(result)
        assert result["message"] == "[external log details omitted]"
        assert result["logger"] == "external"
        assert result["line"] == 23


def test_unknown_nontext_messages_are_not_stringified():
    class Payload:
        def __str__(self):
            raise AssertionError("private objects must never be formatted")

    record = logging.LogRecord("vendor", logging.WARNING, "/vendor/client.py", 1, Payload(), (), None)
    assert "PRIVATE_" not in DiagnosticFormatter().format(record)
