"""Swipe confirmations are bound to the previewed transcript before side effects."""

import copy
from types import SimpleNamespace

import npc_test_support as ns
import pytest
from application_test_setup import make_test_application_services, make_test_delivery_port, make_test_request_context
from settings_test_support import SettingsBuilder

from bridge import callback_dispatch, callbacks, conversation_callbacks, response_delivery, telegram
from bridge.response_variants import save_response_variant
from bridge.sqlite_store import write_transaction
from bridge.swipe_panels import send_swipe_menu


@pytest.fixture
def swipe_case(monkeypatch):
    db = ns.db()
    settings = SettingsBuilder().build()
    case = SimpleNamespace(db=db, calls=[], feedback=[], panel_id=55, before_request=None)

    def request(_token, method, payload):
        assert not db.in_transaction, "Telegram I/O must follow the local selection commit"
        if case.before_request:
            case.before_request(method, payload)
        case.calls.append((method, copy.deepcopy(payload)))
        return {"message_id": payload.get("message_id") or case.panel_id}

    for module in (telegram, callbacks, conversation_callbacks, response_delivery):
        monkeypatch.setattr(module, "telegram_request", request)
    monkeypatch.setattr(callback_dispatch, "answer_callback", lambda _t, _id, value: case.feedback.append(value))
    monkeypatch.setattr(conversation_callbacks, "card_fields_from_file", lambda *a, **k: ns.fields())
    case.delivery = make_test_delivery_port(send_panel_request=telegram.send_panel_request)
    case.services = make_test_application_services(app_settings=settings, delivery=case.delivery)
    case.context = make_test_request_context(db, "s1", "owner", app_settings=settings)
    case.user = ns.turn(db, "user", "First question", 1)
    ns.turn(db, "assistant", "First answer", 2)
    db.execute("UPDATE messages SET telegram_message_ids='[101]' WHERE role='assistant'")
    db.commit()
    save_response_variant(db, "chat", "s1", "First question", "First answer", case.user)
    yield case
    db.close()


def open_panel(case, *, actor="owner"):
    context = make_test_request_context(case.db, "s1", actor, app_settings=case.services.config)
    send_swipe_menu("token", case.db, "chat", "s1", delivery_port=case.delivery, request_context=context)
    return case.panel_id


def callback(case, action, *, message_id=None, actor="owner"):
    message_id = message_id or case.panel_id
    rendered = next(payload for method, payload in reversed(case.calls) if method in {"sendMessage", "editMessageText"})
    data = next(
        button["callback_data"]
        for row in rendered["reply_markup"]["inline_keyboard"]
        for button in row
        if button["callback_data"].split(":")[1] == action
    )
    assert 1 <= len(data.encode("utf-8")) <= 64
    return {
        "id": "swipe-fixture",
        "from": {"id": actor},
        "message": {"chat": {"id": "chat"}, "message_id": message_id},
        "data": data,
    }


def transcript(case):
    return case.db.execute("SELECT id,role,content,telegram_message_ids FROM messages ORDER BY id").fetchall()


@pytest.mark.parametrize("change", ["next_turn", "regenerate", "continue"])
def test_old_swipe_cannot_change_a_newer_transcript(swipe_case, change):
    case = swipe_case
    open_panel(case)
    keep = callback(case, "keep")
    with write_transaction(case.db):
        if change == "continue":
            case.db.execute("UPDATE messages SET content=content || ' Continued scene.' WHERE role='assistant'")
        else:
            user = case.user
            if change == "next_turn":
                user = ns.turn(case.db, "user", "Second question", 3)
                save_response_variant(case.db, "chat", "s1", "Second question", "Discarded answer", user)
            else:
                case.db.execute("DELETE FROM messages WHERE role='assistant'")
            ns.turn(case.db, "assistant", "Current accepted answer", 4)
            case.db.execute("UPDATE messages SET telegram_message_ids='[102]' WHERE id=(SELECT MAX(id) FROM messages)")
            prompt = "Second question" if change == "next_turn" else "First question"
            save_response_variant(case.db, "chat", "s1", prompt, "Current accepted answer", user)
    before = transcript(case)
    case.calls.clear()

    callback_dispatch.process_callback(case.db, "token", keep, services=case.services)

    assert transcript(case) == before
    assert not any(method in {"deleteMessage", "editMessageText"} for method, _payload in case.calls)
    assert any("changed" in message.casefold() or "expired" in message.casefold() for message in case.feedback)


@pytest.mark.parametrize("lifecycle", ["resolution_committed", "closed"])
def test_swipe_preserves_closing_story_before_telegram_deletion(swipe_case, lifecycle):
    case = swipe_case
    open_panel(case)
    keep = callback(case, "keep")
    with write_transaction(case.db):
        case.db.execute("INSERT INTO ending_state(chat_id,session_id,lifecycle) VALUES('chat','s1',?)", (lifecycle,))
    before = transcript(case)
    case.calls.clear()

    callback_dispatch.process_callback(case.db, "token", keep, services=case.services)

    assert transcript(case) == before
    assert not any(method in {"deleteMessage", "editMessageText"} for method, _payload in case.calls)
    assert any("ended" in message or "epilogue" in message for message in case.feedback)


def test_current_swipe_commits_before_removing_previous_telegram_answer(swipe_case):
    case = swipe_case
    with write_transaction(case.db):
        case.db.execute("UPDATE messages SET content='Second answer' WHERE role='assistant'")
        save_response_variant(case.db, "chat", "s1", "First question", "Second answer", case.user)
    open_panel(case)
    previous = callback(case, "prev")
    callback_dispatch.process_callback(case.db, "token", previous, services=case.services)
    keep = callback(case, "keep")

    def assert_committed(method, payload):
        if method == "deleteMessage" and payload["message_id"] == 101:
            assert case.db.execute("SELECT content FROM messages WHERE role='assistant'").fetchone() == (
                "First answer",
            )

    case.before_request = assert_committed
    callback_dispatch.process_callback(case.db, "token", keep, services=case.services)

    assert case.db.execute("SELECT content FROM messages WHERE role='assistant'").fetchone() == ("First answer",)
    assert any(method == "deleteMessage" and payload["message_id"] == 101 for method, payload in case.calls)


def test_old_panel_cannot_use_another_panels_selection(swipe_case):
    case = swipe_case
    open_panel(case)
    old_keep = callback(case, "keep")
    case.panel_id = 56
    open_panel(case, actor="other-owner")
    before = transcript(case)
    case.calls.clear()

    callback_dispatch.process_callback(case.db, "token", old_keep, services=case.services)

    assert transcript(case) == before
    assert not any(method in {"deleteMessage", "editMessageText"} for method, _payload in case.calls)
