"""Reserved protocol cleanup preserves real story commits and renderer envelopes."""

import json

import pytest
from application_test_setup import make_test_application_services, make_test_provider_port
from test_light_novel_flow import make_started
from test_light_novel_storage import novel_db as novel_db
from test_npc_generation_wiring import _fields

from bridge import (
    continuation,
    edit_messages,
    generation,
    image_messages,
    light_novel_turn,
    message_commands,
    regeneration,
)
from bridge.light_novel_service import prepare_turn
from bridge.response_variants import save_response_variant
from bridge.sqlite_store import write_transaction


@pytest.mark.parametrize("route", ["ordinary", "edit", "regen", "continue", "image"])
@pytest.mark.parametrize("wrapped", [False, True])
def test_story_routes_parse_reintroduced_envelopes_before_suppressing_private_blocks(
    novel_db, monkeypatch, route, wrapped
):
    db, session, settings = make_started(novel_db, "a")
    session.update(_actor_id="owner", response_language="en", humanizer="off")
    original_choices = ["Open the door", "Wait outside", "Look around"]
    calls = []

    def generate(_key, _model, messages, **kwargs):
        assert "SECRET" not in str(messages)
        calls.append(kwargs.get("session_id"))
        if str(kwargs.get("session_id") or "").endswith(":language-render"):
            response = json.dumps({"story": "Visible continuation.<internal_states>RENDER_SECRET", "choices": []})
            return "<final>" + response + "</final>" if wrapped else response
        return json.dumps({"story": "Initial story.<internal_states>RAW_SECRET", "choices": original_choices})

    services = make_test_application_services(
        app_settings=settings, provider=make_test_provider_port(generate_backend=generate)
    )
    with write_transaction(db):
        user = db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('chat','story','user','Go',1)"
        ).lastrowid
        db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) "
            "VALUES('chat','story','assistant','Old visible ending.<internal_states>OLD_SECRET',2)"
        )
        save_response_variant(db, "chat", "story", "Go", "Old visible ending.<internal_states>OLD_SECRET", user)
    for owner in (message_commands, light_novel_turn):
        monkeypatch.setattr(
            owner,
            "prepare_turn",
            lambda db, chat, sess, key, actor: prepare_turn(db, chat, sess, key, actor, rng=lambda _: 3),
        )
    for owner in (message_commands, edit_messages, image_messages):
        for name in ("send_typing", "send_reply", "queue_user_quote_tts", "telegram_request"):
            if hasattr(owner, name):
                monkeypatch.setattr(owner, name, lambda *a, **k: {})
    monkeypatch.setattr(
        "bridge.telegram.urllib.request.urlopen", lambda *a, **k: pytest.fail("Unexpected external delivery")
    )
    common = dict(
        provider_port=services.provider,
        memory_service=services.memory,
        npc_service=services.npc,
        persona_service=services.persona,
        rag_service=services.rag,
        app_settings=settings,
    )
    if route == "ordinary":
        message_commands.generate_and_store_reply(
            db,
            "token",
            "key",
            _fields(),
            "chat",
            "Go",
            session,
            "story",
            session["model_id"],
            None,
            "",
            21,
            None,
            group_service=services.group,
            **common,
        )
    elif route == "edit":
        edit_messages.regenerate_edited_turn(db, "token", "key", session, _fields(), "chat", user, "Go again", **common)
    elif route == "regen":
        regeneration.regenerate_last(
            db, "token", "key", session, _fields(), "chat", delivery_port=services.delivery, **common
        )
    elif route == "continue":
        continuation.continue_last(
            db, "token", "key", session, _fields(), "chat", delivery_port=services.delivery, **common
        )
    else:
        image_messages.process_image_message(
            db,
            "token",
            "key",
            session,
            _fields(),
            "chat",
            "Go",
            b"synthetic",
            telegram_message_id=21,
            group_service=services.group,
            group_director_service=services.group_director,
            **common,
        )
    saved = db.execute("SELECT content FROM messages WHERE role='assistant' ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert "Visible continuation." in saved and "SECRET" not in saved and '"story"' not in saved
    assert ("Old visible ending." in saved) is (route == "continue")
    assert len(calls) == 2
    record = db.execute("SELECT choices_json FROM light_novel_choice_sets ORDER BY id DESC LIMIT 1").fetchone()
    assert json.loads(record[0]) == original_choices
    variant = db.execute("SELECT response FROM response_variants WHERE selected=1 ORDER BY id DESC LIMIT 1").fetchone()
    assert variant[0] == saved


def test_shared_renderer_cleans_between_language_and_humanizer_calls():
    seen = []

    def generate(_key, _model, messages, **kwargs):
        source = messages[-1]["content"]
        seen.append(source)
        assert "SECRET" not in source
        if kwargs["session_id"].endswith(":language-render"):
            return "Visible scene.<internal_states>LANGUAGE_SECRET"
        return "Visible scene.<internal_states>HUMANIZER_SECRET"

    result = generation.render_session_response(
        "key",
        {"session_id": "s", "model_id": "m", "response_language": "en", "humanizer": "on"},
        "Visible scene.<internal_states>RAW_SECRET",
        "c",
        {},
        provider_port=make_test_provider_port(generate_backend=generate),
    )
    assert len(seen) == 2 and all("SECRET" not in source for source in seen)
    assert "Visible scene." in result and "SECRET" not in result


def test_reserved_only_response_does_not_become_an_empty_story():
    with pytest.raises(ValueError, match="no visible narrative"):
        generation.render_session_response(
            "key",
            {"session_id": "s", "model_id": "m", "response_language": "auto"},
            "<internal_states>SECRET",
            "c",
            {},
            provider_port=make_test_provider_port(generate_backend=lambda *a, **k: pytest.fail("Unexpected rewrite")),
        )
