"""Choice validation keeps exact limits and never exposes rejected text."""

import json

import pytest
from test_light_novel_recovery import failure_events, pending_choice
from test_light_novel_storage import novel_db as novel_db

from bridge.diagnostic_logging import DiagnosticFormatter
from bridge.light_novel_format import parse_story_response_diagnostic, validate_choices
from bridge.light_novel_service import ensure_choices
from bridge.provider_port import ProviderPort


@pytest.mark.parametrize(
    ("invalid", "reason"),
    [
        ("", "empty_choice"),
        ("PRIVATE_LONG_" + "x" * 161, "choice_too_long"),
        ("/PRIVATE_COMMAND", "command_like_choice"),
        ("@PRIVATE_ACTOR", "command_like_choice"),
    ],
)
def test_choice_repair_reports_specific_private_validation_reason(novel_db, caplog, invalid, reason):
    db, session, settings = novel_db
    record = pending_choice(db, session, 3)
    calls = []

    def generate(_key, _model, messages, **kwargs):
        calls.append(messages)
        choices = [invalid, "Second action", "Third action"] if len(calls) == 1 else ["One", "Two", "Three"]
        return json.dumps({"choices": choices})

    result = ensure_choices(db, record.nonce, session, {}, provider_port=ProviderPort(generate), app_settings=settings)
    assert result.generation_status == "ready"
    assert len(calls) == 2
    assert failure_events(caplog)[0]["reason"] == reason
    assert reason in calls[1][0]["content"]
    assert "Aim for 60–120 characters" in calls[0][0]["content"]
    assert "PRIVATE_" not in calls[1][0]["content"]
    rendered = "\n".join(DiagnosticFormatter().format(r) for r in caplog.records)
    assert "PRIVATE_" not in rendered


def test_choice_character_limit_remains_160():
    valid = ["A" * 160, "B", "C"]
    assert validate_choices(valid, 3) == valid
    with pytest.raises(ValueError):
        validate_choices(["A" * 161, "B", "C"], 3)


@pytest.mark.parametrize(
    ("invalid", "reason"),
    [
        ("", "empty_choice"),
        ("x" * 161, "choice_too_long"),
        ("/command", "command_like_choice"),
        ("@actor", "command_like_choice"),
    ],
)
def test_inline_validation_preserves_story_and_specific_reason(invalid, reason):
    source = json.dumps({"story": "Preserved narrative", "choices": [invalid, "B", "C"]})
    assert parse_story_response_diagnostic(source, 3) == ("Preserved narrative", None, reason, 3)
