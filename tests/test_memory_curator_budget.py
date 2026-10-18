"""Curator output budgets are explicit, validated, and independent of Story settings."""

import io
import json
import time
from functools import partial
from types import SimpleNamespace

import pytest
from application_test_setup import make_test_provider_port
from settings_test_support import make_test_settings

import bridge.provider_transport as transport
from bridge.config_values import ConfigurationError
from bridge.generation_settings import get_generation_settings, update_generation_settings
from bridge.memory_curator import extract_curator_segment
from bridge.memory_response import memory_failure_code
from bridge.memory_store import next_source_segment
from bridge.model_router import ModelRouter
from bridge.model_selection import set_task_model
from bridge.provider_port import ProviderPort
from bridge.session_naming import create_session
from bridge.settings import load_app_settings
from bridge.sqlite_store import db_connect


def test_curator_budget_defaults_to_previous_limit(tmp_path):
    assert load_app_settings({}, home=tmp_path).memory_curator_max_tokens == 1400


@pytest.mark.parametrize("raw, expected", [("1", 1), ("4096", 4096), ("16000", 16000), (" 4096 ", 4096)])
def test_curator_budget_accepts_bounded_integer_override(tmp_path, raw, expected):
    settings = load_app_settings({"SILLYTAVERN_MEMORY_CURATOR_MAX_TOKENS": raw}, home=tmp_path)
    assert settings.memory_curator_max_tokens == expected


@pytest.mark.parametrize("raw", ["0", "-1", "16001", "1.5", "", "private-invalid-value"])
def test_curator_budget_rejects_invalid_config_without_exposing_value(tmp_path, raw):
    with pytest.raises(ConfigurationError, match="SILLYTAVERN_MEMORY_CURATOR_MAX_TOKENS") as error:
        load_app_settings({"SILLYTAVERN_MEMORY_CURATOR_MAX_TOKENS": raw}, home=tmp_path)
    assert "between 1 and 16000" in str(error.value)
    assert "private-invalid-value" not in str(error.value)


def test_curator_budget_is_an_independent_environment_snapshot(tmp_path, monkeypatch):
    supplied = {"SILLYTAVERN_MEMORY_CURATOR_MAX_TOKENS": "4096"}
    first = load_app_settings(supplied, home=tmp_path)
    supplied["SILLYTAVERN_MEMORY_CURATOR_MAX_TOKENS"] = "8000"
    second = load_app_settings(supplied, home=tmp_path)
    monkeypatch.setenv("SILLYTAVERN_MEMORY_CURATOR_MAX_TOKENS", "16000")
    assert first.memory_curator_max_tokens == 4096
    assert second.memory_curator_max_tokens == 8000
    assert load_app_settings({}, home=tmp_path).memory_curator_max_tokens == 1400


@pytest.mark.parametrize("environ, expected", [({}, 1400), ({"SILLYTAVERN_MEMORY_CURATOR_MAX_TOKENS": "4096"}, 4096)])
def test_curator_request_uses_its_budget_without_mutating_story(tmp_path, environ, expected):
    settings = make_test_settings(environ=environ, home=tmp_path)
    db = db_connect(app_settings=settings)
    calls = []

    def generate(_key, model, _messages, **kwargs):
        calls.append((model, kwargs))
        return '{"memories":[{"key":"fixture.goal","text":"Find the lost map.","kind":"goal"}]}'

    try:
        session = create_session(db, "fixture-chat", "fixture::story", app_settings=settings)
        set_task_model(db, "fixture-chat", session["session_id"], "fixture::utility")
        update_generation_settings(db, "fixture-chat", session["session_id"], max_tokens=8000)
        source = SimpleNamespace(role="user", start_id=1, start_offset=0, end_offset=18, content="Find the lost map.")
        result = extract_curator_segment(
            db,
            "fixture-chat",
            session,
            "Fixture character",
            {"memories": []},
            source,
            provider_port=make_test_provider_port(generate_backend=generate),
            app_settings=settings,
        )
        assert result["memories"][0]["key"] == "fixture.goal"
        assert len(calls) == 1
        model, request = calls[0]
        assert model == "fixture::utility"
        assert request["settings"]["max_tokens"] == expected
        assert request["force_non_stream"] is True
        assert get_generation_settings(db, "fixture-chat", session["session_id"])["max_tokens"] == 8000
    finally:
        db.close()


@pytest.mark.parametrize("content, succeeds", [('{"memories":[]}', True), ("{}", False)])
def test_curator_budget_reaches_transport_and_keeps_schema_validation(tmp_path, monkeypatch, content, succeeds):
    settings = make_test_settings(
        environ={
            "SILLYTAVERN_MEMORY_CURATOR_MAX_TOKENS": "4096",
            "SILLYTAVERN_PROVIDER_ALLOWED_HOSTS": "provider.example",
            "FIXTURE_KEY": "synthetic-key",
        },
        home=tmp_path,
    )
    catalog = {
        "fixture": {
            "transport": "openai_compatible",
            "api_endpoint": "https://provider.example/v1",
            "api_key_env": "FIXTURE_KEY",
            "models": ["model"],
        }
    }
    router = ModelRouter(load_catalog=lambda: catalog)
    port = ProviderPort(partial(transport.generate_provider_text, router, app_settings=settings))
    sent = []

    def respond(request, timeout, *, environ):
        sent.append(json.loads(request.data))
        return io.BytesIO(
            json.dumps({"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}).encode()
        )

    monkeypatch.setattr(transport, "strict_urlopen", respond)
    db = db_connect(app_settings=settings)
    try:
        session = create_session(db, "fixture-chat", "fixture::model", app_settings=settings)
        db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("fixture-chat", session["session_id"], "user", "Find the lost map.", time.time()),
        )
        db.commit()
        source = next_source_segment(db, "fixture-chat", session["session_id"], "curator")

        def extract():
            return extract_curator_segment(
                db,
                "fixture-chat",
                session,
                "Fixture character",
                {"memories": []},
                source,
                provider_port=port,
                app_settings=settings,
            )

        if succeeds:
            assert extract() == {"memories": []}
        else:
            with pytest.raises(ValueError, match="Curator response requires a memories array") as error:
                extract()
            assert memory_failure_code(error.value) == "invalid_shape"
        assert len(sent) == (1 if succeeds else 2)
        assert all(request["max_tokens"] == 4096 for request in sent)
        assert all(request["model"] == "model" for request in sent)
        assert all(request["stream"] is False for request in sent)
    finally:
        db.close()
