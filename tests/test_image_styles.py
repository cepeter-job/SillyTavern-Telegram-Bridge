from __future__ import annotations

import base64
import io
import json
from types import SimpleNamespace

import pytest
from application_test_setup import ensure_application_extensions, make_test_provider_port
from settings_test_support import make_test_settings

ensure_application_extensions()

from bridge import feature_callbacks, image_callbacks, image_generation, image_panels, image_routing
from bridge.image_reference import ImageReference
from bridge.metadata import get_meta, set_meta
from bridge.session_naming import create_session
from bridge.sqlite_store import db_connect


@pytest.fixture
def image_session(tmp_path):
    catalog = tmp_path / "providers.yaml"
    catalog.write_text(
        "providers:\n"
        "  images:\n"
        "    api_endpoint: https://images.example/v1\n"
        "    image_enabled: true\n"
        "    image_models: [z-image-turbo, step-image-edit-2]\n"
        "    image_model_capabilities:\n"
        "      step-image-edit-2:\n"
        "        mode: reference\n",
        encoding="utf-8",
    )
    native_settings = tmp_path / "settings.json"
    native_settings.write_text("{}", encoding="utf-8")
    settings = make_test_settings(
        {"SILLYTAVERN_PROVIDER_ALLOWED_HOSTS": "images.example"},
        home=tmp_path,
        db_file=tmp_path / "bridge.sqlite3",
        provider_config_file=catalog,
        character_dir=tmp_path / "characters",
        default_character_file="Mira.png",
        native_persona_settings_file=native_settings,
    )
    settings.character_dir.mkdir()
    db = db_connect(app_settings=settings)
    session = create_session(db, "chat", "story::main", session_id="scene", app_settings=settings)
    db.execute(
        "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
        ("chat", "scene", "assistant", "Mira stands beside an oak.", 1.0),
    )
    db.commit()
    try:
        yield SimpleNamespace(db=db, session=session, settings=settings)
    finally:
        db.close()


def _panel(state, *, chat_id="chat", session_id="scene"):
    return image_panels.imagine_panel(state.db, chat_id, session_id, app_settings=state.settings)


def _callback(state, action, monkeypatch):
    panels, answers = [], []
    monkeypatch.setattr(image_panels, "_send", lambda *args, **kwargs: panels.append((args[2], args[3])))
    callback = {"id": "cb", "message": {"message_id": 77}}
    handled = feature_callbacks.handle_feature_panel_callback(
        state.db,
        "token",
        callback,
        lambda _t, _id, text: answers.append(text),
        f"imagine:{action}",
        "chat",
        callback["message"],
        state.session,
        "scene",
        None,
        group_service=None,
        provider_port=make_test_provider_port(),
        delivery_port=None,
        request_context=SimpleNamespace(app_settings=state.settings),
    )
    assert handled
    return panels, answers


def test_imagine_menu_exposes_both_styles_and_marks_default(image_session):
    text, markup = _panel(image_session)
    styles = {
        button["callback_data"]: button["text"]
        for row in markup["inline_keyboard"]
        for button in row
        if button["callback_data"].startswith("imagine:style:")
    }
    assert set(styles) == {"imagine:style:realism", "imagine:style:anime"}
    assert "✅" in styles["imagine:style:realism"]
    assert "✅" not in styles["imagine:style:anime"]
    assert "Style: Realism" in text


def test_style_callbacks_persist_selection_without_changing_model_or_size(image_session, monkeypatch):
    state = image_session
    image_routing.set_session_image_model(
        state.db, "chat", "scene", "images::z-image-turbo", app_settings=state.settings
    )
    image_routing.set_session_image_size(state.db, "chat", "scene", "1024x1536")
    for style, label in (("anime", "Anime"), ("realism", "Realism")):
        panels, _answers = _callback(state, f"style:{style}", monkeypatch)
        assert get_meta(state.db, "image_style:chat:scene") == style
        assert len(panels) == 1
        assert f"Style: {label}" in panels[0][0]
        checked = [
            button["text"] for row in panels[0][1]["inline_keyboard"] for button in row if "✅" in button["text"]
        ]
        assert len(checked) == 1 and label in checked[0]
        assert "Style: Realism" in _panel(state, session_id="other")[0]
        assert "Style: Realism" in _panel(state, chat_id="other")[0]
    assert image_routing.session_image_settings(state.db, "chat", "scene", app_settings=state.settings) == (
        "images::z-image-turbo",
        "1024x1536",
    )
    reopened = db_connect(app_settings=state.settings)
    try:
        assert get_meta(reopened, "image_style:chat:scene") == "realism"
    finally:
        reopened.close()


@pytest.mark.parametrize("invalid", ["", "watercolor", "anime:realism"])
def test_invalid_style_callback_retains_previous_selection(image_session, monkeypatch, invalid):
    state = image_session
    set_meta(state.db, "image_style:chat:scene", "anime")
    panels, answers = _callback(state, f"style:{invalid}", monkeypatch)
    assert get_meta(state.db, "image_style:chat:scene") == "anime"
    assert answers and "unsupported" in answers[0].casefold()
    assert len(panels) == 1 and "Style: Anime" in panels[0][0]


def test_image_reset_restores_default_style(image_session, monkeypatch):
    state = image_session
    set_meta(state.db, "image_style:chat:scene", "anime")
    panels, _answers = _callback(state, "reset", monkeypatch)
    assert get_meta(state.db, "image_style:chat:scene") in {"", "realism"}
    assert len(panels) == 1 and "Style: Realism" in panels[0][0]


def test_unknown_persisted_style_falls_back_to_realism(image_session):
    set_meta(image_session.db, "image_style:chat:scene", "removed-style")
    assert "Style: Realism" in _panel(image_session)[0]


def _capture_generation(state, monkeypatch, *, transport, visual_prompt="Mira stands beside an oak."):
    requests, photos, utility_messages = [], [], []
    model = "step-image-edit-2" if transport == "reference" else "z-image-turbo"
    image_routing.set_session_image_model(state.db, "chat", "scene", f"images::{model}", app_settings=state.settings)
    reference = ImageReference(b"reference-png", "image/png", "Mira.png") if transport == "reference" else None
    monkeypatch.setattr(image_generation, "load_character_reference", lambda *args, **kwargs: reference)
    monkeypatch.setattr(image_generation, "send_text", lambda *args: [71])
    monkeypatch.setattr(image_generation, "telegram_request", lambda *args: {})
    monkeypatch.setattr(image_generation, "_multipart_photo", lambda *args: photos.append((args[2], args[3])))

    def request(request, **kwargs):
        if transport == "reference":
            prompt = request.data.split(b'name="prompt"\r\n\r\n', 1)[1].split(b"\r\n", 1)[0].decode()
        else:
            prompt = json.loads(request.data)["prompt"]
        requests.append((request.full_url, prompt))
        return io.BytesIO(json.dumps({"data": [{"b64_json": base64.b64encode(b"image-bytes").decode()}]}).encode())

    def utility(_key, _model, messages, **kwargs):
        utility_messages.append(messages)
        return visual_prompt

    monkeypatch.setattr(image_generation, "strict_urlopen", request)
    return requests, photos, utility_messages, make_test_provider_port(generate_backend=utility)


@pytest.mark.parametrize("style, marker", [("realism", "photorealistic"), ("anime", "cel shading")])
@pytest.mark.parametrize("action", ["scene", "custom"])
@pytest.mark.parametrize("transport", ["text", "reference"])
def test_selected_style_reaches_image_provider_for_both_generation_modes(
    image_session,
    monkeypatch,
    style,
    marker,
    action,
    transport,
):
    state = image_session
    set_meta(state.db, "image_style:chat:scene", style)
    requests, photos, utility_messages, provider = _capture_generation(state, monkeypatch, transport=transport)
    before = state.db.execute("SELECT role,content FROM messages ORDER BY rowid").fetchall()
    if action == "scene":
        image_generation.handle_imagine_scene(
            state.db,
            "token",
            "chat",
            state.session,
            provider_port=provider,
            app_settings=state.settings,
        )
    else:
        image_generation.handle_imagine_custom_prompt(
            state.db,
            "token",
            "chat",
            state.session,
            "Mira stands beside an oak.",
            app_settings=state.settings,
        )
    assert len(requests) == 1
    endpoint, prompt = requests[0]
    assert marker in prompt.casefold()
    assert style in prompt.casefold()
    assert "override source medium" in prompt.casefold()
    assert "Mira stands beside an oak." in prompt
    assert endpoint.endswith("/images/edits" if transport == "reference" else "/images/generations")
    assert ("identity reference" in prompt) == (transport == "reference")
    if action == "scene":
        assert marker in utility_messages[0][0]["content"].casefold()
    if style == "anime":
        assert "photorealistic" not in prompt.casefold()
    assert photos == [(b"image-bytes", "")]
    assert state.db.execute("SELECT role,content FROM messages ORDER BY rowid").fetchall() == before


@pytest.mark.parametrize("style", ["realism", "anime"])
@pytest.mark.parametrize("transport, model_limit", [("text", 1200), ("reference", 512)])
def test_custom_input_hint_matches_style_and_reference_prompt_budget(
    image_session,
    monkeypatch,
    style,
    transport,
    model_limit,
):
    state = image_session
    set_meta(state.db, "image_style:chat:scene", style)
    requests, _photos, _messages, _provider = _capture_generation(state, monkeypatch, transport=transport)
    inputs = []
    monkeypatch.setattr(image_callbacks, "start_text_action_input", lambda *args: inputs.append(args))
    _callback(state, "custom", monkeypatch)
    hint = inputs[0][5]
    assert style in hint.casefold()
    allowed = int(hint.split("1–", 1)[1].split(" ", 1)[0].replace(",", ""))
    assert 0 < allowed < model_limit
    image_generation.handle_imagine_custom_prompt(
        state.db,
        "token",
        "chat",
        state.session,
        "x" * allowed,
        app_settings=state.settings,
    )
    assert len(requests) == 1 and len(requests[0][1]) == model_limit
    assert style in requests[0][1].casefold()
    with pytest.raises(ValueError, match="prompt"):
        image_generation.handle_imagine_custom_prompt(
            state.db,
            "token",
            "chat",
            state.session,
            "x" * (allowed + 1),
            app_settings=state.settings,
        )
    assert len(requests) == 1


@pytest.mark.parametrize("transport, model_limit", [("text", 1200), ("reference", 512)])
def test_scene_prompt_truncation_keeps_anime_directive(image_session, monkeypatch, transport, model_limit):
    state = image_session
    set_meta(state.db, "image_style:chat:scene", "anime")
    requests, _photos, _messages, provider = _capture_generation(
        state,
        monkeypatch,
        transport=transport,
        visual_prompt="x" * 6000,
    )
    image_generation.handle_imagine_scene(
        state.db,
        "token",
        "chat",
        state.session,
        provider_port=provider,
        app_settings=state.settings,
    )
    assert len(requests) == 1
    assert len(requests[0][1]) == model_limit
    assert "cel shading" in requests[0][1]
    assert ("identity reference" in requests[0][1]) == (transport == "reference")


def test_style_selection_remains_available_without_image_provider(image_session, monkeypatch):
    image_session.settings.provider_config_file.write_text("providers: {}\n", encoding="utf-8")
    panels, _answers = _callback(image_session, "style:anime", monkeypatch)
    assert get_meta(image_session.db, "image_style:chat:scene") == "anime"
    assert "Style: Anime" in panels[0][0]
    assert "Model: Not configured" in panels[0][0]


def test_checkpoint_capture_includes_session_image_style(image_session):
    from bridge.narrative_checkpoint_capture import capture_pre_finale_state

    state = image_session
    set_meta(state.db, "image_style:chat:scene", "anime")
    checkpoint = capture_pre_finale_state(state.db, "chat", "scene", 1)
    assert checkpoint["config"]["preferences"].get("image_style") == "anime"


def test_checkpoint_restore_accepts_image_style_without_changing_source(image_session):
    from bridge.alternate_ending_restore import restore_checkpoint_state
    from bridge.narrative_checkpoint_capture import capture_pre_finale_state
    from bridge.narrative_checkpoints import NarrativeCheckpoint
    from bridge.sqlite_store import write_transaction

    state = image_session
    snapshot = capture_pre_finale_state(state.db, "chat", "scene", 1)
    snapshot["config"]["preferences"]["image_style"] = "anime"
    checkpoint = NarrativeCheckpoint("test-checkpoint", 0, 1, snapshot)
    create_session(state.db, "chat", "story::main", session_id="target", app_settings=state.settings)
    set_meta(state.db, "image_style:chat:scene", "realism")
    with write_transaction(state.db):
        restore_checkpoint_state(state.db, "chat", "target", checkpoint, {0: 0, 1: 1})
    assert get_meta(state.db, "image_style:chat:target") == "anime"
    assert get_meta(state.db, "image_style:chat:scene") == "realism"
