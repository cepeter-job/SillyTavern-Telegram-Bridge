"""Plain-text usage command regressions through the real conversation route."""

from unittest.mock import Mock

import pytest
from application_test_setup import (
    ensure_application_extensions,
    make_test_conversation_service,
    make_test_delivery_port,
    make_test_provider_port,
)
from test_memory_completion_safety import session_db as session_db
from test_npc_generation_wiring import _fields

from bridge.conversation_lifecycle import conversation_state, mark_started
from bridge.sqlite_store import write_transaction
from bridge.token_usage_repository import insert_event

NOW = 2_000_000_000.0
DAY = 86400


def add_usage(db, **overrides):
    event = {
        "chat_id": "chat",
        "session_id": "s1",
        "model": "example::story-model",
        "purpose": "story",
        "created_at": NOW,
        "status": "succeeded",
        "elapsed_ms": 100,
        "input_tokens": 1000,
        "output_tokens": 200,
        "total_tokens": 1200,
        "cached_tokens": 300,
        "reasoning_tokens": 50,
        "reported": True,
        "complete": True,
    }
    event.update(overrides)
    with write_transaction(db):
        insert_event(db, **event)


@pytest.fixture
def command_case(session_db, monkeypatch):
    config, db, session = session_db
    mark_started(db, "chat", "s1", conversation_state(db, "chat", "s1").epoch)
    monkeypatch.setattr("bridge.message_commands.card_fields_from_file", lambda *a, **k: _fields())
    ensure_application_extensions()
    sent = Mock(return_value=[123])
    monkeypatch.setattr("bridge.command_routes.send_text", sent)
    monkeypatch.setattr("bridge.telegram.send_text", sent)
    monkeypatch.setattr(
        "bridge.telegram.urllib.request.urlopen", lambda *a, **k: pytest.fail("Unexpected network call")
    )
    service = make_test_conversation_service(
        app_settings=config,
        delivery=make_test_delivery_port(send_text=sent),
        provider=make_test_provider_port(generate_backend=lambda *a, **k: pytest.fail("Usage must not call a model")),
    )
    return config, db, session, service, sent


def invoke(case, command="/usage", chat_id="chat", session_id="s1"):
    _, db, session, service, _ = case
    service.process_message(
        db,
        "token",
        "key",
        session["model_id"],
        _fields(),
        chat_id,
        command,
        queued_session_id=session_id,
        actor_id="owner",
    )


def test_usage_empty_ledger_is_plain_text_without_generation(command_case):
    _, db, _, _, sent = command_case
    invoke(command_case)
    sent.assert_called_once()
    text = sent.call_args.args[2]
    assert "Token usage" in text
    assert "last 7 days" in text
    assert "No provider calls recorded" in text
    assert "not a billing statement" in text
    assert sent.call_args.kwargs == {}
    assert db.execute("SELECT COUNT(*) FROM messages").fetchone() == (0,)
    assert db.execute("SELECT COUNT(*) FROM token_usage_events").fetchone() == (0,)


def test_usage_reports_totals_coverage_and_breakdowns(command_case, monkeypatch):
    _, db, _, _, sent = command_case
    monkeypatch.setattr("bridge.usage_panels.time.time", lambda: NOW)
    add_usage(db)
    before = db.execute("SELECT * FROM token_usage_events").fetchall()
    invoke(command_case)
    text = sent.call_args.args[2]
    assert "Calls: 1" in text
    assert "Reported: 1/1" in text
    assert "Complete: 1/1" in text
    assert "Failed: 0" in text
    assert "Cancelled: 0" in text
    assert "Input: 1,000" in text
    assert "Output: 200" in text
    assert "Total: 1,200" in text
    assert "Cached: 300" in text
    assert "Reasoning: 50" in text
    assert "subsets" in text
    assert "Daily (UTC)" in text
    assert "Top models" in text and "example::story-model" in text
    assert "Top purposes" in text and "story" in text
    assert db.execute("SELECT * FROM token_usage_events").fetchall() == before
    assert db.execute("SELECT COUNT(*) FROM messages").fetchone() == (0,)


@pytest.mark.parametrize(
    "overrides, expected_input, expected_total, incomplete",
    [
        (
            {"input_tokens": None, "output_tokens": None, "total_tokens": None, "reported": False, "complete": False},
            "unknown",
            "unknown",
            True,
        ),
        ({"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}, "0", "0", False),
        ({"input_tokens": 13, "output_tokens": None, "total_tokens": None, "complete": False}, "13", "unknown", True),
    ],
)
def test_unknown_partial_and_zero_usage_remain_distinct(
    command_case, monkeypatch, overrides, expected_input, expected_total, incomplete
):
    _, db, _, _, sent = command_case
    monkeypatch.setattr("bridge.usage_panels.time.time", lambda: NOW)
    add_usage(db, cached_tokens=None, reasoning_tokens=None, **overrides)
    invoke(command_case)
    text = sent.call_args.args[2]
    assert f"Input: {expected_input}\n" in text
    assert f"Total: {expected_total}\n" in text
    assert "Cached: unknown" in text
    assert "Reasoning: unknown" in text
    assert ("totals may be incomplete" in text) is incomplete


def test_usage_is_discoverable_in_help_and_bot_menu(monkeypatch):
    from bridge.bot_commands import set_bot_commands
    from bridge.help_details import COMMAND_DETAILS, HELP_CATEGORIES, help_text

    commands = [command for command, _ in HELP_CATEGORIES["basic"]]
    assert "/usage" in commands
    text = help_text("basic", commands.index("/usage"))
    assert "last 7 days" in text
    assert "active session" in text
    assert "unknown" in COMMAND_DETAILS["/usage"]
    request = Mock()
    monkeypatch.setattr("bridge.bot_commands.telegram_request", request)
    set_bot_commands("token")
    menu = request.call_args.args[2]["commands"]
    assert [item["command"] for item in menu].count("usage") == 1
    assert "7-day" in next(item["description"] for item in menu if item["command"] == "usage")


def test_usage_isolated_to_chat_session_and_rolling_window(command_case, monkeypatch):
    _, db, _, _, sent = command_case
    monkeypatch.setattr("bridge.usage_panels.time.time", lambda: NOW)
    for created_at in (NOW - 7 * DAY, NOW - DAY, NOW):
        add_usage(db, created_at=created_at)
    for overrides in (
        {"chat_id": "other-chat", "model": "private-chat-model"},
        {"chat_id": "chat:topic:2", "model": "private-topic-model"},
        {"session_id": "other-session", "model": "private-session-model"},
        {"created_at": NOW - 7 * DAY - 1, "model": "expired-model"},
        {"created_at": NOW + 1, "model": "future-model"},
    ):
        add_usage(db, **overrides)
    invoke(command_case)
    text = sent.call_args.args[2]
    assert "Calls: 3" in text
    assert f"Total: {3 * 1200:,}" in text
    for excluded in (
        "private-chat-model",
        "private-topic-model",
        "private-session-model",
        "expired-model",
        "future-model",
    ):
        assert excluded not in text
    from datetime import UTC, datetime

    for stamp in (NOW - 7 * DAY, NOW - DAY, NOW):
        assert datetime.fromtimestamp(stamp, UTC).strftime("%Y-%m-%d") in text


def test_usage_reports_failed_cancelled_and_missing_calls_without_inventing_tokens(command_case, monkeypatch):
    _, db, _, _, sent = command_case
    monkeypatch.setattr("bridge.usage_panels.time.time", lambda: NOW)
    add_usage(db)
    add_usage(db, status="failed", complete=False, model="example::failed", purpose="choices")
    add_usage(
        db,
        status="cancelled",
        complete=False,
        reported=False,
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
        cached_tokens=None,
        reasoning_tokens=None,
        model="example::cancelled",
        purpose="summary",
    )
    invoke(command_case)
    text = sent.call_args.args[2]
    assert "Calls: 3 · Reported: 2/3 · Complete: 1/3" in text
    assert "Failed: 1 · Cancelled: 1" in text
    assert f"Total: {2 * 1200:,}" in text
    assert "example::failed" in text and "choices" in text
    assert "example::cancelled" in text and "summary" in text
    assert "totals may be incomplete" in text


@pytest.mark.parametrize("command", ["/usage", "/usage@bridge_bot", "@bridge_bot /usage", "/usage extra"])
def test_usage_works_before_start_with_normalized_commands(command_case, command):
    from bridge.conversation_lifecycle import reset_conversation

    _, db, _, _, sent = command_case
    reset_conversation(db, "chat", "s1")
    invoke(command_case, command)
    assert "Token usage" in sent.call_args.args[2]
    assert not conversation_state(db, "chat", "s1").started
    assert db.execute("SELECT COUNT(*) FROM messages").fetchone() == (0,)


def test_usage_remains_available_for_completed_stories(command_case):
    from test_epilogue_service import complete, producer_for, resolved

    config, db, session, _, sent = command_case
    case = (config, db, session)
    resolved(case)
    assert complete(case, producer_for(db, [])).lifecycle == "closed"
    before = db.execute("SELECT * FROM messages").fetchall()
    invoke(command_case)
    assert "Token usage" in sent.call_args.args[2]
    assert db.execute("SELECT * FROM messages").fetchall() == before


def test_usage_limits_breakdowns_and_normalizes_long_labels(command_case, monkeypatch):
    _, db, _, _, sent = command_case
    monkeypatch.setattr("bridge.usage_panels.time.time", lambda: NOW)
    for index in range(8):
        add_usage(db, model=f"model-{index}", purpose=f"purpose-{index}", total_tokens=index * 1200)
    invoke(command_case)
    text = sent.call_args.args[2]
    for index in range(3):
        assert f"• model-{index}:" not in text
        assert f"• purpose-{index}:" not in text
    for index in range(3, 8):
        assert f"• model-{index}:" in text
        assert f"• purpose-{index}:" in text
    sent.reset_mock()
    long_model = "huge\n\t" + "x" * 250
    add_usage(db, model=long_model, purpose="long\n\tpurpose", total_tokens=99_000)
    with write_transaction(db):
        db.execute("UPDATE sessions SET title=? WHERE chat_id='chat' AND session_id='s1'", ("name\n" + "z" * 1000,))
    invoke(command_case)
    text = sent.call_args.args[2]
    assert "huge " in text and "long purpose" in text
    assert "huge\n" not in text and "name\n" not in text
    assert "…" in text
    assert len(text) < 4096
    sent.assert_called_once()


def test_other_command_prefix_does_not_match_usage(command_case):
    invoke(command_case, "/usagefoo")
    assert "Unknown or removed command" in command_case[-1].call_args.args[2]
