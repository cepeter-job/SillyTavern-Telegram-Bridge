"""Shared policy at actual Story, Light Novel, Group and status boundaries."""

import json
from contextlib import closing
from pathlib import Path

import pytest
from application_test_setup import (
    make_test_application_services,
    make_test_delivery_port,
    make_test_group_service,
    make_test_memory_service,
    make_test_persona_service,
    make_test_provider_port,
    make_test_rag_service,
)
from test_light_novel_flow import attached_choice, bridge_services
from test_light_novel_storage import novel_db as novel_db
from test_narrative_reconciliation import proposal
from test_npc_branch_safety import _db, _fields, _session, _turn

from bridge.conversation_lifecycle import conversation_state, mark_started
from bridge.generation import build_chat_messages
from bridge.metadata import set_meta
from bridge.narrative_context import narrative_context_for_session
from bridge.narrative_reconciliation import reconcile_narrative_state_now
from bridge.narrative_settings import preset_narrative_settings, save_session_narrative_settings
from bridge.npc_service import NpcService
from bridge.settings import load_app_settings
from bridge.sqlite_store import write_transaction


@pytest.fixture
def story_case(tmp_path):
    settings = load_app_settings({}, home=tmp_path)
    with closing(_db()) as db:
        session = _session()
        session["grounded_user"] = "on"
        mark_started(db, "chat", "s1", conversation_state(db, "chat", "s1").epoch)
        save_session_narrative_settings(db, "chat", "s1", preset_narrative_settings("world_driven"))
        set_meta(db, "stream_mode:chat", "off")
        with write_transaction(db):
            user_row = _turn(db, "user", "Wait and watch.", 1)
            _turn(db, "assistant", "Mara watches the gate, away from the user.", 2)
        yield db, session, settings, user_row


@pytest.mark.parametrize("preset", ["player_centric", "ensemble", "world_driven", "observer"])
def test_story_prompt_has_one_narrative_policy_independent_of_grounding(story_case, preset):
    db, session, settings, _ = story_case
    save_session_narrative_settings(db, "chat", "s1", preset_narrative_settings(preset))
    messages = build_chat_messages(
        session,
        _fields(),
        "Continue the scene.",
        [],
        persona_service=make_test_persona_service(),
        narrative_context=narrative_context_for_session(db, "chat", "s1", "story"),
        app_settings=settings,
    )
    system = messages[0]["content"]
    assert system.count("## Narrative Policy") == 1
    assert system.count("## Grounded User Policy") == 1
    assert "already-established user decision" in system
    assert "Never invent the user's dialogue" in system
    assert "Telegram Roleplay Output Contract" in system
    if preset in {"world_driven", "observer"}:
        assert "no forced return to the user" in system
    elif preset == "player_centric":
        assert "main protagonist" in system


def invoke_story_flow(flow, case, monkeypatch):
    from bridge import continuation, edit_messages, image_messages, message_commands, regeneration

    db, session, settings, user_row = case
    captured = []
    for owner in (edit_messages, image_messages, message_commands):
        for name in ("send_text", "send_typing", "send_reply", "telegram_request"):
            if hasattr(owner, name):
                monkeypatch.setattr(owner, name, lambda *a, **k: {})
    monkeypatch.setattr(message_commands, "queue_user_quote_tts", lambda *a, **k: None)
    provider = make_test_provider_port(
        generate_backend=lambda _a, _m, messages, **k: captured.append(messages) or "Mara waits."
    )
    common = {
        "provider_port": provider,
        "memory_service": make_test_memory_service(),
        "npc_service": NpcService(),
        "persona_service": make_test_persona_service(),
        "app_settings": settings,
        "rag_service": make_test_rag_service(),
    }
    if flow == "text":
        message_commands.generate_and_store_reply(
            db,
            "token",
            "",
            _fields(),
            "chat",
            "Follow Mara.",
            session,
            session["session_id"],
            session["model_id"],
            None,
            "",
            99,
            None,
            group_service=make_test_group_service(app_settings=settings),
            **common,
        )
    elif flow == "edit":
        edit_messages.regenerate_edited_turn(
            db, "token", "", session, _fields(), "chat", user_row, "Stay outside.", **common
        )
    elif flow == "image":
        image_messages.process_image_message(
            db,
            "token",
            "",
            session,
            _fields(),
            "chat",
            "Watch this location.",
            b"fixture image",
            group_service=make_test_group_service(app_settings=settings),
            group_director_service=make_test_application_services(app_settings=settings).group_director,
            **common,
        )
    else:
        owner = continuation.continue_last if flow == "continue" else regeneration.regenerate_last
        owner(db, "token", "", session, _fields(), "chat", delivery_port=make_test_delivery_port(), **common)
    return captured


@pytest.mark.parametrize("flow", ["text", "regen", "continue", "edit", "image"])
def test_every_story_generation_path_receives_session_policy(story_case, monkeypatch, flow):
    calls = invoke_story_flow(flow, story_case, monkeypatch)
    assert len(calls) == 1
    system = calls[0][0]["content"]
    assert "## Narrative Policy" in system
    assert "no forced return to the user" in system
    assert "harmless connective movement only" in system
    assert "## Grounded User Policy" in system


@pytest.mark.parametrize("flow", ["regen", "edit"])
def test_replacing_a_turn_does_not_inject_its_discarded_future_scene(story_case, monkeypatch, flow):
    db, session, settings, _ = story_case
    reconcile_narrative_state_now(
        db,
        "",
        "chat",
        session,
        provider_port=make_test_provider_port(generate_backend=lambda *a, **k: proposal("discarded-future-scene")),
        app_settings=settings,
    )
    assert "discarded-future-scene" in narrative_context_for_session(db, "chat", "s1", "story")
    calls = invoke_story_flow(flow, story_case, monkeypatch)
    assert "## Narrative Policy" in calls[0][0]["content"]
    assert "discarded-future-scene" not in calls[0][0]["content"]


@pytest.mark.parametrize("strategy", ["a", "b", "c"])
def test_choice_only_generation_and_inline_repair_follow_offscreen_policy(novel_db, strategy):
    from bridge.light_novel_service import ensure_choices

    db, session, settings = novel_db
    save_session_narrative_settings(db, "chat", "story", preset_narrative_settings("world_driven"))
    record = attached_choice(novel_db, strategy=strategy, ready=False)
    reconcile_narrative_state_now(
        db,
        "",
        "chat",
        session,
        provider_port=make_test_provider_port(generate_backend=lambda *a, **k: proposal()),
        app_settings=settings,
    )
    calls = []
    result = ensure_choices(
        db,
        record.nonce,
        session,
        _fields(),
        provider_port=make_test_provider_port(
            generate_backend=lambda _a, _m, messages, **k: (
                calls.append(messages) or '{"choices":["Follow Mara","Cut to camp","Shift focus"]}'
            )
        ),
        app_settings=settings,
    )
    assert result.generation_status == "ready"
    assert len(calls) == 1
    system = calls[0][0]["content"]
    assert "narrative steering" in system.lower()
    assert "do not fabricate off-screen user actions" in system
    assert "the USER can choose in this scene" not in system
    assert "For in-world user actions only" in system
    assert "For narrative steering choices" in system
    assert "Phrase each action from the USER" not in system


def test_inline_story_choices_receive_narrative_style(novel_db):
    from test_light_novel_flow import make_started

    from bridge.light_novel_turn import begin_novel_turn

    db, session, _settings = make_started(novel_db, "a")
    save_session_narrative_settings(db, "chat", "story", preset_narrative_settings("observer"))
    turn = begin_novel_turn(db, "chat", session, "message", 92)
    messages = turn.messages([{"role": "system", "content": "Character and world facts."}], "auto")
    assert "cinematic/objective" in messages[0]["content"]
    assert "narrative steering" in messages[0]["content"].lower()
    assert "Character and world facts." in messages[0]["content"]
    assert "For in-world user actions only" in messages[0]["content"]
    assert "For narrative steering choices" in messages[0]["content"]
    assert "Phrase each action from the USER" not in messages[0]["content"]


@pytest.mark.parametrize("action", ["lnchoice", "lnnext"])
def test_offscreen_choice_handoff_is_steering_not_user_dialogue(novel_db, action):
    from bridge.light_novel_callbacks import route_light_novel_callback
    from bridge.light_novel_repository import bind_choice_panel

    db, _session_value, settings = novel_db
    save_session_narrative_settings(db, "chat", "story", preset_narrative_settings("observer"))
    record = attached_choice(novel_db, choices=["Follow Mara", "Follow the distant council", "Shift focus"])
    with write_transaction(db):
        bind_choice_panel(db, record.nonce, 81)
    callback = {
        "id": "choice",
        "message": {"message_id": 81},
        "data": f"{action}:{record.nonce}" + (":0" if action == "lnchoice" else ""),
    }
    sent = []
    services = bridge_services(settings, sent)
    route_light_novel_callback(services, db, callback, 90, "chat", "owner", message_worker=lambda *a: None)
    payload = json.loads(db.execute("SELECT payload_json FROM jobs WHERE update_id=90").fetchone()[0])
    assert payload["narrative_input"] == "steering"
    assert "[Narrative steering]" in payload["text"]
    assert "until the user character can meaningfully participate again" not in payload["text"]


def test_group_speaker_selection_receives_policy_through_its_injected_contract(story_case):
    from bridge.director_goals import director_goal_policy
    from bridge.group_director_service import GroupDirectorService

    db, session, _settings, _ = story_case
    group = {"enabled": True, "mode": "director", "members": ["Mara.png", "Boris.png"], "turn_index": 0}
    service = GroupDirectorService(
        load_group_state=lambda *a: group,
        safe_character=Path,
        member_labels=lambda names: names,
        card_fields=lambda name: {"name": Path(name).stem},
        director_policy=director_goal_policy,
    )
    before = db.execute("SELECT * FROM messages").fetchall()
    result = service.plan(db, "chat", session)
    assert result[0] == "Mara.png"
    context = service.prompt_context(db, "chat", session, result[0])
    assert "no forced return to the user" in context
    assert "not to recenter the user" in context
    assert db.execute("SELECT * FROM messages").fetchall() == before


def test_status_shows_style_and_freshness_but_not_hidden_directions(story_case):
    from bridge.status_panels import status_text

    db, session, settings, _ = story_case
    with write_transaction(db):
        db.execute(
            "INSERT INTO director_state(chat_id,session_id,goal,active_direction) "
            "VALUES('chat','s1','SECRET_OBJECTIVE','SECRET_FUTURE_DIRECTION')"
        )
    text = status_text(
        db,
        "chat",
        session,
        _fields(),
        session["model_id"],
        "",
        group_service=make_test_group_service(app_settings=settings),
        app_settings=settings,
    )
    assert "World-driven" in text
    assert "Narrative state: stale" in text
    assert "SECRET_" not in text


@pytest.mark.parametrize("write_kind", ["save", "panel"])
def test_changing_session_style_invalidates_choices_but_default_save_does_not(novel_db, write_kind):
    from bridge.light_novel_repository import load_choice_set
    from bridge.narrative_repository import narrative_settings_revision
    from bridge.narrative_settings import apply_narrative_preference, save_user_narrative_default

    db, _session_value, _settings = novel_db
    record = attached_choice(novel_db)
    save_user_narrative_default(db, "owner", preset_narrative_settings("observer"))
    assert load_choice_set(db, record.nonce).state == "open"
    if write_kind == "save":
        save_session_narrative_settings(db, "chat", "story", preset_narrative_settings("observer"))
    else:
        apply_narrative_preference(
            db,
            "chat",
            "story",
            actor_id="owner",
            expected_revision=narrative_settings_revision(db, "chat", "story"),
            action="preset",
            value="observer",
        )
    assert load_choice_set(db, record.nonce).state != "open"


def test_free_offscreen_custom_style_is_steering_when_user_presence_is_unknown(novel_db):
    from bridge.narrative_context import narrative_choice_is_steering
    from bridge.narrative_settings import normalize_narrative_settings

    db, _session_value, _settings = novel_db
    save_session_narrative_settings(
        db, "chat", "story", normalize_narrative_settings({"preset": "custom", "offscreen_policy": "free"})
    )
    assert narrative_choice_is_steering(db, "chat", "story")


def test_offscreen_choice_panel_uses_reader_steering_heading(novel_db, monkeypatch):
    from bridge import light_novel_panels

    db, _session_value, settings = novel_db
    save_session_narrative_settings(db, "chat", "story", preset_narrative_settings("observer"))
    record = attached_choice(novel_db, choices=["Follow Mara", "Cut to the council", "Shift focus"])
    sent = []
    monkeypatch.setattr(
        light_novel_panels, "send_panel_request", lambda _t, _m, p, **k: sent.append(p) or {"message_id": 81}
    )
    light_novel_panels.render_choices(db, "token", record, app_settings=settings)
    assert sent[-1]["text"].startswith("Where should the story go next?")
    assert "What will you do?" not in sent[-1]["text"]


def test_steering_input_remains_out_of_world_in_current_and_historical_turns(story_case):
    from bridge.narrative_values import NARRATIVE_STEERING_PREFIX

    db, session, settings, _ = story_case
    steering = NARRATIVE_STEERING_PREFIX + "Follow Mara at the gate."
    messages = build_chat_messages(
        session,
        _fields(),
        steering,
        [("user", steering)],
        persona_service=make_test_persona_service(),
        app_settings=settings,
        narrative_context=narrative_context_for_session(db, "chat", "s1", "story"),
    )
    user_messages = [m["content"] for m in messages if m["role"] == "user"]
    assert len(user_messages) == 2
    for text in user_messages:
        assert "Out-of-world narrative steering" in text
        assert "not character dialogue" in text
        assert "Follow Mara at the gate." in text
        assert "[Narrative steering]" not in text
