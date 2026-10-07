"""Every interactive memory search uses the composed bounded recall facade."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from application_test_setup import make_test_memory_service
from settings_test_support import make_test_settings
from test_miniapp_memory import setup
from test_story_memory_scope import db as db

from bridge.memory_contracts import MemorySearchResult


def search_service(calls):
    def search(db, chat_id, session, query, character_name="", max_tokens=1600):
        assert not db.in_transaction
        calls.append((chat_id, session["session_id"], query, character_name, max_tokens))
        return [MemorySearchResult(f"native-{index}", f"Local fact {index}", "world") for index in range(15)]

    return replace(make_test_memory_service(), search_backend=search)


@pytest.mark.parametrize("route", ["command", "text action", "direct command"])
def test_memory_search_routes_share_facade_and_keep_five_local_results(db, monkeypatch, route):
    from bridge import command_panels, memory, text_action_input

    calls, sent = [], []
    service = search_service(calls)
    settings = make_test_settings()
    session, fields = {"session_id": "s"}, {"name": "Mira"}

    def sender(*args):
        sent.append(args[-1])

    context = SimpleNamespace(app_settings=settings)
    if route == "direct command":
        memory.handle_memory_command(
            db,
            "",
            "c",
            session,
            fields,
            "/memory search silver key",
            send_text_fn=sender,
            app_settings=settings,
            memory_service=service,
        )
    elif route == "command":
        assert command_panels._handle_memory_media(
            db,
            "",
            "",
            "c",
            "/memory search silver key",
            "/memory search silver key",
            session,
            fields,
            None,
            request_context=context,
            delivery_port=SimpleNamespace(send_text=sender),
            memory_service=service,
            group_service=None,
            npc_service=None,
            persona_service=None,
            provider_port=None,
            sync_service=None,
            rag_service=None,
        )
    else:
        monkeypatch.setattr(text_action_input, "send_text", sender)
        monkeypatch.setattr(text_action_input, "send_memory_menu", lambda *args, **kwargs: None)
        monkeypatch.setattr(text_action_input, "_cancel_pending", lambda *args: None)
        assert text_action_input._handle_text_action_input(
            db,
            "",
            "",
            "c",
            session,
            fields,
            "silver key",
            {"action": "memory_search"},
            None,
            request_context=context,
            memory_service=service,
            provider_port=None,
            npc_service=None,
            persona_service=None,
            rag_service=None,
        )
    assert calls == [("c", "s", "silver key", "Mira", 1600)]
    assert sent == [
        "Recalled memories:\n- Local fact 0\n- Local fact 1\n- Local fact 2\n- Local fact 3\n- Local fact 4"
    ]


def test_miniapp_recall_uses_same_facade_and_keeps_ten_result_format(tmp_path):
    from bridge.miniapp_memory import recall_remote

    services, who, values = setup(tmp_path)
    calls = []
    services.memory = search_service(calls)
    result = recall_remote(services, who, {**values, "query": "silver key"})
    assert result == {"results": [f"Local fact {index}" for index in range(10)]}
    assert calls[0][:4] == (who.chat_id, values["session_id"], "silver key", "Default")
