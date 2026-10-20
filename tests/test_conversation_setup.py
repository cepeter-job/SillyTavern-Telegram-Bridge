import pytest
from application_test_setup import make_test_persona_service
from character_test_support import card_png as _card_png
from test_light_novel_storage import novel_db as novel_db

from bridge.conversation_lifecycle import conversation_state, mark_started


@pytest.fixture
def setup(novel_db):
    from bridge.conversation_setup import ConversationSetupService

    db, session, settings = novel_db
    settings.character_dir.mkdir(parents=True, exist_ok=True)
    (settings.character_dir / "Alice.png").write_bytes(_card_png("Alice", "The scene"))
    service = ConversationSetupService(settings, make_test_persona_service())
    state = service.begin(db, "chat", session, "owner", "Alice.png")
    return db, session, service, state


def choose(setup, stage, value="", action="pick"):
    db, session, service, state = setup
    return service.choose(db, "chat", session, "owner", state["nonce"], stage, action, value)


def ready(setup, mode="normal", preset="player_centric"):
    choose(setup, "narrative", preset)
    choose(setup, "mode", mode)
    if mode == "lightnovel":
        choose(setup, "strategy", "b")
    choose(setup, "persona", "")
    choose(setup, "world", "", "off")
    choose(setup, "system_prompt", "")
    return choose(setup, "session", "story")


def test_normal_wizard_skips_strategy_and_does_not_mutate_session(setup):
    db, _, service, state = setup
    original = db.execute("SELECT * FROM sessions").fetchall()
    choose(setup, "narrative", "player_centric")
    assert choose(setup, "mode", "normal")["stage"] == "persona"
    assert db.execute("SELECT * FROM sessions").fetchall() == original
    assert not conversation_state(db, "chat", "story").started
    assert service.load(db, "chat", "story", "owner")["nonce"] == state["nonce"]


def test_light_novel_wizard_requires_strategy(setup):
    choose(setup, "narrative", "player_centric")
    assert choose(setup, "mode", "lightnovel")["stage"] == "strategy"
    with pytest.raises(ValueError):
        choose(setup, "persona", "")
    with pytest.raises(ValueError):
        choose(setup, "strategy", "d")
    assert choose(setup, "strategy", "c")["stage"] == "persona"


def test_setup_apply_atomic_with_mode_and_unstarted_target(setup):
    db, session, service, state = setup
    ready(setup, "lightnovel")
    result = service.apply(db, "chat", session, "owner", state["nonce"])
    assert result["session_id"] == "story"
    assert db.execute("SELECT character_file FROM sessions WHERE session_id='story'").fetchone() == ("Alice.png",)
    mode = conversation_state(db, "chat", "story")
    assert (mode.mode, mode.strategy, mode.started) == ("lightnovel", "b", False)
    with pytest.raises(ValueError):
        service.load(db, "chat", "story", "owner")


def test_setup_actor_and_source_epoch_are_bound(setup):
    from bridge.conversation_lifecycle import reset_conversation

    db, _, service, _ = setup
    with pytest.raises(ValueError):
        service.load(db, "chat", "story", "thief")
    reset_conversation(db, "chat", "story")
    with pytest.raises(ValueError):
        choose(setup, "mode", "normal")


def test_setup_started_target_rejected_without_partial_changes(setup):
    db, session, service, state = setup
    ready(setup)
    mark_started(db, "chat", "story", conversation_state(db, "chat", "story").epoch)
    original = db.execute("SELECT * FROM sessions").fetchall()
    with pytest.raises(ValueError, match="already started"):
        service.apply(db, "chat", session, "owner", state["nonce"])
    assert db.execute("SELECT * FROM sessions").fetchall() == original


def test_deleted_resource_refuses_whole_setup(setup):
    db, session, service, state = setup
    ready(setup)
    (service.app_settings.character_dir / "Alice.png").unlink()
    original = db.execute("SELECT * FROM sessions").fetchall()
    with pytest.raises(ValueError):
        service.apply(db, "chat", session, "owner", state["nonce"])
    assert db.execute("SELECT * FROM sessions").fetchall() == original


def test_setup_new_session_created_only_at_apply(setup):
    db, session, service, state = setup
    ready(setup)
    service.back(db, "chat", session, "owner", state["nonce"])
    assert choose(setup, "session", "$new")["stage"] == "session_name"
    assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1
    service.set_title(db, "chat", session, "owner", "A new adventure")
    result = service.apply(db, "chat", session, "owner", state["nonce"])
    assert result["title"] == "A new adventure"
    assert result["character_file"] == "Alice.png"
    assert not conversation_state(db, "chat", result["session_id"]).started


def test_setup_off_choices_and_cancel_preserve_configuration(setup):
    db, session, service, state = setup
    ready(setup)
    service.cancel(db, "chat", session, "owner", state["nonce"])
    assert conversation_state(db, "chat", "story").mode == "normal"
    assert db.execute("SELECT character_file FROM sessions WHERE session_id='story'").fetchone()[0] != "Alice.png"


def test_setup_panel_mode_buttons_are_opaque_and_scoped(setup, monkeypatch):
    import json

    from bridge import conversation_setup_panels as panels
    from bridge.callback_tokens import resolve_dynamic_callback_token
    from bridge.request_types import RequestContext

    db, _session, service, state = setup
    state = choose(setup, "narrative", "player_centric")
    sent = []
    monkeypatch.setattr(
        panels, "send_panel_request", lambda token, method, payload, **kw: sent.append(payload) or {"message_id": 55}
    )
    panels.send_setup_panel(
        "token",
        "chat",
        state,
        request_context=RequestContext(db, "story", "owner", app_settings=service.app_settings),
        persona_service=service.persona_service,
    )
    buttons = [button for row in sent[-1]["reply_markup"]["inline_keyboard"] for button in row]
    assert {"Normal", "Light Novel"} <= {button["text"] for button in buttons}
    picked = next(button for button in buttons if button["text"] == "Light Novel")
    token = picked["callback_data"].split(":", 1)[1]
    assert len(picked["callback_data"].encode()) <= 64
    decoded = json.loads(resolve_dynamic_callback_token(token, "conversation_setup", "chat", db=db))
    assert decoded == {"nonce": state["nonce"], "stage": "mode", "action": "pick", "value": "lightnovel"}


def test_setup_callbacks_advance_without_changing_current_session(setup, monkeypatch):
    import json

    from bridge import conversation_setup_callbacks as callbacks
    from bridge.callback_tokens import dynamic_callback_token
    from bridge.request_types import RequestContext

    db, session, service, state = setup
    choose(setup, "narrative", "player_centric")
    data = "setup:" + dynamic_callback_token(
        "conversation_setup",
        json.dumps({"nonce": state["nonce"], "stage": "mode", "action": "pick", "value": "lightnovel"}),
        "chat",
        db=db,
    )
    panels = []
    monkeypatch.setattr(callbacks, "send_setup_panel", lambda *a, **k: panels.append(a[2]))
    handled = callbacks.handle_setup_callback(
        db,
        "token",
        {"id": "callback"},
        lambda *a: None,
        data,
        "chat",
        {"message_id": 55},
        session,
        persona_service=service.persona_service,
        request_context=RequestContext(db, "story", "owner", app_settings=service.app_settings),
    )
    assert handled
    assert panels[-1]["stage"] == "strategy"
    assert conversation_state(db, "chat", "story").mode == "normal"


@pytest.mark.parametrize("error_type", [TimeoutError, RuntimeError])
@pytest.mark.parametrize("error_stage", ["notice", "ack"])
def test_setup_apply_notice_failure_does_not_report_expired(setup, monkeypatch, caplog, error_type, error_stage):
    import json

    from bridge import conversation_setup_callbacks as callbacks
    from bridge.callback_tokens import dynamic_callback_token
    from bridge.conversation_setup_state import setup_key
    from bridge.metadata import get_meta
    from bridge.request_types import RequestContext

    db, session, service, state = setup
    ready(setup, "lightnovel")
    data = "setup:" + dynamic_callback_token(
        "conversation_setup",
        json.dumps({"nonce": state["nonce"], "stage": "review", "action": "apply"}),
        "chat",
        db=db,
    )
    attempted_notices, acknowledgements = [], []

    def send_notice(token, chat_id, text):
        attempted_notices.append(text)
        if error_stage == "notice" and text.startswith("Session configured:"):
            raise error_type("private transport detail")

    def answer(token, callback_id, text):
        acknowledgements.append(text)
        if error_stage == "ack":
            raise error_type("private transport detail")

    monkeypatch.setattr(callbacks, "close_panel_message", lambda *a: None)
    monkeypatch.setattr(callbacks, "send_text", send_notice)
    caplog.set_level("INFO", logger="bridge.events")
    handled = callbacks.handle_setup_callback(
        db,
        "token",
        {"id": "callback", "_queued": True},
        answer,
        data,
        "chat",
        {"message_id": 55},
        session,
        persona_service=service.persona_service,
        request_context=RequestContext(db, "story", "owner", app_settings=service.app_settings),
    )

    assert handled
    assert acknowledgements == ["Setup updated"]
    assert len(attempted_notices) == 1
    assert attempted_notices[0].startswith("Session configured:")
    assert get_meta(db, setup_key("chat", "owner"), "") == ""
    assert db.execute("SELECT character_file FROM sessions WHERE session_id='story'").fetchone() == ("Alice.png",)
    mode = conversation_state(db, "chat", "story")
    assert (mode.mode, mode.strategy, mode.started) == ("lightnovel", "b", False)
    failures = [
        record.diagnostic_fields
        for record in caplog.records
        if getattr(record, "diagnostic_fields", {}).get("event") == "conversation_setup.notification_failed"
    ]
    assert failures == [
        {
            "event": "conversation_setup.notification_failed",
            "phase": "applied",
            "error_type": error_type.__name__,
        }
    ]
    assert "private transport detail" not in caplog.text


def test_applied_setup_notice_failure_completes_durable_job_without_replay(setup, tmp_path, monkeypatch):
    import json
    from unittest.mock import Mock

    from application_test_setup import make_test_application_services

    from bridge import conversation_setup_callbacks as callbacks
    from bridge import job_store, worker_orchestration
    from bridge.callback_tokens import dynamic_callback_token
    from bridge.composition import BackgroundRuntime, BridgeServices, TelegramRuntime
    from bridge.job_service import JobService
    from bridge.panel_bindings import bind_panel_session
    from bridge.sqlite_store import db_connect

    db, session, service, state = setup
    ready(setup, "lightnovel")
    service.back(db, "chat", session, "owner", state["nonce"])
    choose(setup, "session", "$new")
    service.set_title(db, "chat", session, "owner", "A new adventure")
    data = "setup:" + dynamic_callback_token(
        "conversation_setup",
        json.dumps({"nonce": state["nonce"], "stage": "review", "action": "apply"}),
        "chat",
        db=db,
    )
    callback = {
        "id": "callback",
        "data": data,
        "from": {"id": "owner"},
        "message": {"chat": {"id": "chat"}, "message_id": 55},
        "_queued": True,
    }
    bind_panel_session(db, "chat", 55, "story", "owner")
    job_id = job_store.enqueue_job(db, 1, "chat", "story", 55, "callback", {"actor_id": "owner", "callback": callback})
    job_store.mark_job_scheduled(db, job_id)
    notices, failures = [], []

    def send_notice(token, chat_id, text):
        notices.append(text)
        raise TimeoutError("private transport detail")

    monkeypatch.setattr(callbacks, "close_panel_message", lambda *a: None)
    monkeypatch.setattr(callbacks, "send_text", send_notice)
    services = BridgeServices(
        **vars(make_test_application_services(app_settings=service.app_settings, persona=service.persona_service)),
        db_factory=lambda: db_connect(tmp_path / "novel.sqlite3", app_settings=service.app_settings),
        jobs=JobService(
            job_store.enqueue_job,
            job_store.store_job_payload,
            job_store.job_actor_id,
            job_store.mark_job_scheduled,
            job_store.mark_job_running,
            job_store.finish_job,
            job_store.recover_jobs,
            Mock(return_value=True),
        ),
        telegram=TelegramRuntime(
            request=lambda *a, **k: {},
            send_text=lambda *a, **k: failures.append(a) or [],
            download_file=lambda *a, **k: b"",
        ),
        background=BackgroundRuntime(
            submit_chat=Mock(return_value=True),
            submit=Mock(return_value=True),
            register_backlog_dispatcher=lambda callback: None,
            begin_shutdown=lambda: None,
        ),
    )

    worker_orchestration.process_callback_job(services, "chat", callback, job_id)

    assert db.execute("SELECT state,attempts FROM jobs WHERE job_id=?", (job_id,)).fetchone() == ("done", 1)
    assert len(notices) == 1 and notices[0].startswith("Session configured:")
    assert not failures
    configured_sessions = db.execute("SELECT * FROM sessions ORDER BY session_id").fetchall()
    assert len(configured_sessions) == 2

    worker_orchestration.process_callback_job(services, "chat", callback, job_id)

    assert db.execute("SELECT * FROM sessions ORDER BY session_id").fetchall() == configured_sessions
    assert db.execute("SELECT state,attempts FROM jobs WHERE job_id=?", (job_id,)).fetchone() == ("done", 1)
    assert len(notices) == 1
    assert not failures
