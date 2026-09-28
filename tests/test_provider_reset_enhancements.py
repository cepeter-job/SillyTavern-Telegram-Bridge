import time

from application_test_setup import make_test_memory_service, make_test_request_context
from settings_test_support import make_test_settings

from bridge import cards, model_selection, provider_panels, response_delivery, settings_input, telegram
from bridge.conversation_lifecycle import configure_conversation, conversation_state, mark_started, reset_conversation
from bridge.generation_settings import get_generation_settings, update_generation_settings
from bridge.light_novel_repository import attach_choice_set, bind_choice_panel, consume_choice_set, reserve_choice_set
from bridge.light_novel_service import attach_turn, ensure_choices, prepare_turn
from bridge.memory import generate_session_summary
from bridge.message_commands import reset_session
from bridge.provider_port import ProviderPort
from bridge.session_core import create_session
from bridge.session_naming import start_session_name_input
from bridge.sqlite_store import db_connect, write_transaction


def session_fixture(tmp_path):
    settings = make_test_settings(home=tmp_path)
    db = db_connect(tmp_path / "feature.sqlite3", app_settings=settings)
    session = create_session(db, "chat", "story::main", session_id="story", title="Story", app_settings=settings)
    return settings, db, session


def utility_reasoning_api():
    setter = getattr(model_selection, "set_utility_reasoning", None)
    getter = getattr(model_selection, "utility_reasoning_for_session", None)
    assert callable(setter), "utility reasoning setter is missing"
    assert callable(getter), "utility reasoning reader is missing"
    return setter, getter


def test_provider_target_panel_quotes_models_and_both_reasoning_levels(tmp_path, monkeypatch):
    settings, db, session = session_fixture(tmp_path)
    try:
        setter, _getter = utility_reasoning_api()
        update_generation_settings(db, "chat", session["session_id"], reasoning_budget=4096)
        setter(db, "chat", session["session_id"], 1024)
        captured = []
        monkeypatch.setattr(
            cards,
            "send_panel_request",
            lambda _token, method, payload, **_kwargs: captured.append((method, payload)) or {"message_id": 71},
        )
        provider_panels.send_model_target_menu(
            "token",
            "chat",
            "story::main",
            "utility::worker",
            request_context=make_test_request_context(db, session["session_id"], "owner", app_settings=settings),
        )
        payload = captured[-1][1]
        quote = (
            "Current models\n"
            "📖 Story: story::main\n"
            "🧠 Story reasoning: Medium (4096)\n"
            "🛠 Utility: utility::worker\n"
            "🧠 Utility reasoning: Low (1024)"
        )
        assert payload["text"].startswith(quote + "\n\nConfigure models:")
        blockquote = next(entity for entity in payload["entities"] if entity["type"] == "blockquote")
        assert blockquote == {"type": "blockquote", "offset": 0, "length": len(quote.encode("utf-16-le")) // 2}
        buttons = [button for row in payload["reply_markup"]["inline_keyboard"] for button in row]
        labels = [button["text"] for button in buttons]
        callbacks = [button["callback_data"] for button in buttons]
        assert {"📖 Story model", "🛠️ Utility model", "🧠 Utility reasoning"} <= set(labels)
        assert all(not label.startswith("✅ ") for label in labels)
        assert "models:utility-reasoning" in callbacks
    finally:
        db.close()


def test_utility_reasoning_is_session_scoped_and_used_by_summary(tmp_path):
    settings, db, session = session_fixture(tmp_path)
    try:
        setter, getter = utility_reasoning_api()
        setter(db, "chat", session["session_id"], 8192)
        other = create_session(db, "chat", "story::main", session_id="other", title="Other", app_settings=settings)
        assert getter(db, "chat", session["session_id"]) == 8192
        assert getter(db, "chat", other["session_id"]) == 0
        model_selection.set_task_model(db, "chat", session["session_id"], "utility::worker")
        now = time.time()
        db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", session["session_id"], "user", "Remember the blue key.", now),
        )
        db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", session["session_id"], "assistant", "The key is in the drawer.", now + 0.001),
        )
        db.commit()
        calls = []

        def generate(_key, model, _messages, **kwargs):
            calls.append((model, kwargs["settings"]))
            return "Blue key in drawer."

        summary = generate_session_summary(
            db, "chat", session, force=True, provider_port=ProviderPort(generate), app_settings=settings
        )
        assert summary == "Blue key in drawer."
        assert calls[0][0] == "utility::worker"
        assert calls[0][1]["reasoning_budget"] == 8192
    finally:
        db.close()


def test_light_novel_strategy_b_uses_utility_reasoning(tmp_path):
    settings, db, session = session_fixture(tmp_path)
    try:
        setter, _getter = utility_reasoning_api()
        setter(db, "chat", session["session_id"], 16384)
        model_selection.set_task_model(db, "chat", session["session_id"], "utility::worker")
        configure_conversation(db, "chat", session["session_id"], "lightnovel", "b")
        state = conversation_state(db, "chat", session["session_id"])
        assert mark_started(db, "chat", session["session_id"], state.epoch)
        session = {**session, "_actor_id": "owner"}
        record = prepare_turn(db, "chat", session, "message:1", "owner", rng=lambda _choices: 2)
        assert record is not None
        with write_transaction(db):
            rowid = db.execute(
                "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
                ("chat", session["session_id"], "assistant", "The door opens.", 1.0),
            ).lastrowid
            attach_turn(db, record, int(rowid), "The door opens.")
        calls = []

        def generate(_key, model, _messages, **kwargs):
            calls.append((model, kwargs["settings"]))
            return '{"choices":["Go inside","Wait outside"]}'

        result = ensure_choices(
            db, record.nonce, session, {"name": "Alice"}, provider_port=ProviderPort(generate), app_settings=settings
        )
        assert result.generation_status == "ready"
        assert calls[0][0] == "utility::worker"
        assert calls[0][1]["reasoning_budget"] == 16384
    finally:
        db.close()


def test_custom_utility_reasoning_input_does_not_change_story_reasoning(tmp_path, monkeypatch):
    settings, db, session = session_fixture(tmp_path)
    try:
        _setter, getter = utility_reasoning_api()
        update_generation_settings(db, "chat", session["session_id"], reasoning_budget=1024)
        monkeypatch.setattr(settings_input, "send_settings_menu", lambda *args, **kwargs: None)
        reopened = []
        monkeypatch.setattr(
            settings_input,
            "send_model_target_menu",
            lambda *args, **kwargs: reopened.append((args, kwargs)),
            raising=False,
        )
        state = {
            "key": "reasoning_budget",
            "scope": "utility_reasoning",
            "session_id": session["session_id"],
            "expires_at": time.time() + 600,
            "prompt_message_ids": [],
        }
        assert settings_input._handle_settings_input(
            db,
            "token",
            "chat",
            session["session_id"],
            "5000",
            state,
            request_context=make_test_request_context(db, session["session_id"], "owner", app_settings=settings),
        )
        assert get_generation_settings(db, "chat", session["session_id"])["reasoning_budget"] == 1024
        assert getter(db, "chat", session["session_id"]) == 5000
        assert reopened
    finally:
        db.close()


def test_reset_returns_open_and_consumed_light_novel_panel_ids(tmp_path):
    _settings, db, session = session_fixture(tmp_path)
    try:
        with write_transaction(db):
            consumed = reserve_choice_set(
                db, "chat", session["session_id"], 0, "turn-consumed", "b", 2, "owner", "story::main", 1.0
            )
            attach_choice_set(db, consumed.nonce, 1, "digest-a", ["Go", "Stay"])
            bind_choice_panel(db, consumed.nonce, 81)
            consume_choice_set(db, consumed.nonce, 0, "chat", session["session_id"], "owner", 0, 81)
            opened = reserve_choice_set(
                db, "chat", session["session_id"], 0, "turn-open", "b", 2, "owner", "story::main", 2.0
            )
            attach_choice_set(db, opened.nonce, 2, "digest-b", ["Left", "Right"])
            bind_choice_panel(db, opened.nonce, 82)
        panel_ids = reset_conversation(db, "chat", session["session_id"])
        assert sorted(panel_ids) == [81, 82]
    finally:
        db.close()


def test_reset_deletes_tracked_user_messages_but_not_other_sessions(tmp_path, monkeypatch):
    settings, db, session = session_fixture(tmp_path)
    try:
        other = create_session(db, "chat", "story::main", session_id="other", title="Other", app_settings=settings)
        db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,telegram_message_id,created_at) VALUES(?,?,?,?,?,?)",
            ("chat", session["session_id"], "user", "delete me", "41", 1.0),
        )
        db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,telegram_message_id,created_at) VALUES(?,?,?,?,?,?)",
            ("chat", other["session_id"], "user", "keep me", "99", 2.0),
        )
        db.commit()
        calls = []
        monkeypatch.setattr(
            response_delivery,
            "telegram_request",
            lambda _token, method, payload=None: calls.append((method, payload)) or {},
        )
        reset_session(db, "token", "chat", session, memory_service=make_test_memory_service())
        deleted_ids = [int(payload["message_id"]) for method, payload in calls if method == "deleteMessage"]
        assert 41 in deleted_ids
        assert 99 not in deleted_ids
    finally:
        db.close()


def test_new_management_panel_closes_previous_for_same_actor_only(tmp_path, monkeypatch):
    settings, db, session = session_fixture(tmp_path)
    try:
        events = []
        next_id = iter([10, 11, 12])

        def request(_token, method, payload=None):
            events.append((method, dict(payload or {})))
            if method == "sendMessage":
                return {"message_id": next(next_id)}
            return {"message_id": (payload or {}).get("message_id")}

        monkeypatch.setattr(telegram, "telegram_request", request)
        ctx_a = make_test_request_context(db, session["session_id"], "actor-a", app_settings=settings)
        ctx_b = make_test_request_context(db, session["session_id"], "actor-b", app_settings=settings)
        panel = {"inline_keyboard": [[{"text": "A", "callback_data": "a"}]]}
        telegram.send_panel_request(
            "token",
            "sendMessage",
            {"chat_id": "chat", "text": "one", "reply_markup": panel},
            request_context=ctx_a,
        )
        telegram.send_panel_request(
            "token",
            "sendMessage",
            {"chat_id": "chat", "text": "two", "reply_markup": panel},
            request_context=ctx_a,
        )
        assert any(method == "deleteMessage" and payload.get("message_id") == 10 for method, payload in events)
        before = sum(method == "deleteMessage" and payload.get("message_id") == 11 for method, payload in events)
        telegram.send_panel_request(
            "token",
            "sendMessage",
            {"chat_id": "chat", "text": "other actor", "reply_markup": panel},
            request_context=ctx_b,
        )
        after = sum(method == "deleteMessage" and payload.get("message_id") == 11 for method, payload in events)
        assert after == before
    finally:
        db.close()


def test_conversation_choice_panel_is_not_managed_as_command_panel(tmp_path, monkeypatch):
    settings, db, session = session_fixture(tmp_path)
    try:
        events = []
        next_id = iter([20, 21, 22])

        def request(_token, method, payload=None):
            events.append((method, dict(payload or {})))
            if method == "sendMessage":
                return {"message_id": next(next_id)}
            return {"message_id": (payload or {}).get("message_id")}

        monkeypatch.setattr(telegram, "telegram_request", request)
        ctx = make_test_request_context(db, session["session_id"], "owner", app_settings=settings)
        panel = {"inline_keyboard": [[{"text": "A", "callback_data": "a"}]]}
        telegram.send_panel_request(
            "token",
            "sendMessage",
            {"chat_id": "chat", "text": "management", "reply_markup": panel},
            request_context=ctx,
        )
        telegram.send_panel_request(
            "token",
            "sendMessage",
            {"chat_id": "chat", "text": "choice", "reply_markup": panel},
            request_context=ctx,
            track_management=False,
        )
        telegram.send_panel_request(
            "token",
            "sendMessage",
            {"chat_id": "chat", "text": "next", "reply_markup": panel},
            request_context=ctx,
        )
        deleted = [payload.get("message_id") for method, payload in events if method == "deleteMessage"]
        assert 20 in deleted
        assert 21 not in deleted
    finally:
        db.close()


def test_new_session_prompt_closes_management_panel_without_deleting_old_conversation(tmp_path, monkeypatch):
    settings, db, session = session_fixture(tmp_path)
    try:
        db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,telegram_message_id,created_at) VALUES(?,?,?,?,?,?)",
            ("chat", session["session_id"], "user", "old conversation", "77", 1.0),
        )
        db.commit()
        events = []
        next_id = iter([30])

        def request(_token, method, payload=None):
            events.append((method, dict(payload or {})))
            if method == "sendMessage":
                return {"message_id": next(next_id)}
            return {"message_id": (payload or {}).get("message_id")}

        monkeypatch.setattr(telegram, "telegram_request", request)
        ctx = make_test_request_context(db, session["session_id"], "owner", app_settings=settings)
        telegram.send_panel_request(
            "token",
            "sendMessage",
            {
                "chat_id": "chat",
                "text": "old panel",
                "reply_markup": {"inline_keyboard": [[{"text": "x", "callback_data": "x"}]]},
            },
            request_context=ctx,
        )
        monkeypatch.setattr("bridge.session_naming.send_text", lambda *_args, **_kwargs: [31])
        start_session_name_input(
            db,
            "token",
            "chat",
            session,
            group_service=__import__("application_test_setup")
            .make_test_application_services(app_settings=settings)
            .group,
            app_settings=settings,
            request_context=ctx,
        )
        assert any(method == "deleteMessage" and payload.get("message_id") == 30 for method, payload in events)
        assert db.execute(
            "SELECT content FROM messages WHERE chat_id=? AND session_id=? AND role='user'",
            ("chat", session["session_id"]),
        ).fetchone() == ("old conversation",)
    finally:
        db.close()


def test_photo_management_panel_is_closed_by_next_command_panel(tmp_path, monkeypatch):
    settings, db, session = session_fixture(tmp_path)
    try:
        photo = tmp_path / "character.png"
        photo.write_bytes(b"synthetic-png")

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"ok":true,"result":{"message_id":40}}'

        monkeypatch.setattr(telegram.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
        ctx = make_test_request_context(db, session["session_id"], "owner", app_settings=settings)
        telegram.send_panel_photo(
            "token",
            "chat",
            photo,
            "Character info",
            {"inline_keyboard": [[{"text": "Close", "callback_data": "character:cancel"}]]},
            request_context=ctx,
        )
        events = []
        monkeypatch.setattr(
            telegram,
            "telegram_request",
            lambda _token, method, payload=None: (
                events.append((method, dict(payload or {}))) or ({"message_id": 41} if method == "sendMessage" else {})
            ),
        )
        telegram.send_panel_request(
            "token",
            "sendMessage",
            {
                "chat_id": "chat",
                "text": "providers",
                "reply_markup": {"inline_keyboard": [[{"text": "x", "callback_data": "x"}]]},
            },
            request_context=ctx,
        )
        assert any(method == "deleteMessage" and payload.get("message_id") == 40 for method, payload in events)
    finally:
        db.close()


def test_utility_target_catalog_marks_current_utility_model(tmp_path, monkeypatch):
    from bridge import provider_callbacks

    settings, db, session = session_fixture(tmp_path)
    try:
        model_selection.set_task_model(db, "chat", session["session_id"], "utility::worker")
        shown = []
        monkeypatch.setattr(
            provider_callbacks,
            "send_model_menu",
            lambda _token, _chat, current_model, *args, **kwargs: shown.append(current_model),
        )
        answered = []
        assert provider_callbacks.handle_provider_model_callback(
            db,
            "token",
            {"id": "cb"},
            lambda *_args: answered.append(_args),
            "modeltarget:utility",
            "chat",
            {"message_id": 71},
            session,
            session["session_id"],
            None,
            request_context=make_test_request_context(db, session["session_id"], "owner", app_settings=settings),
        )
        assert shown == ["utility::worker"]
    finally:
        db.close()
