"""Director Room API uses the same authenticated, revision-checked application owners."""

import json
from contextlib import closing
from types import SimpleNamespace

import pytest
from miniapp_test_support import identity, make_services

from bridge import miniapp_director as api
from bridge.director_room import director_room
from bridge.director_service import DirectorService
from bridge.miniapp_errors import MiniAppError
from bridge.miniapp_http import api_routes
from bridge.miniapp_models import model_catalog, select_model
from bridge.model_router import ModelRouter
from bridge.model_selection import director_reasoning_for_session, utility_reasoning_for_session
from bridge.narrative_settings import load_session_narrative_settings
from bridge.sqlite_store import write_transaction


def setup(tmp_path):
    services = make_services(tmp_path)
    who = identity()
    data = api.get_director(services, who, {})
    body = {"session_id": data["session"]["session_id"], "revision": data["revision"]}
    return services, who, data, body


def test_get_room_does_not_leak_raw_plan_or_other_user_data(tmp_path):
    services, who, data, body = setup(tmp_path)
    assert data["mutable"] and data["session_id"] == body["session_id"]
    assert data["settings"]["director_cadence_mode"] == "adaptive"
    assert data["director_reasoning"] == 0
    api.edit_direction(services, who, body | {"scope": "persistent", "direction": "PRIVATE objective"})
    other = api.get_director(services, identity("67890"), {"chat_id": who.chat_id, "session_id": body["session_id"]})
    assert "PRIVATE" not in json.dumps(other)
    assert not {"inflight_token", "active_proposal_json", "api_key", "system_prompt"} & data.keys()
    with closing(services.db_factory()) as db:
        assert director_room(db, who.chat_id, body["session_id"])["objective"] == "PRIVATE objective"


def test_edit_uses_canonical_room_and_rejects_stale_revision(tmp_path):
    services, who, data, body = setup(tmp_path)
    before = data["revision"]
    result = api.edit_direction(services, who, body | {"scope": "persistent", "direction": "Follow Mara's search"})
    assert result["saved"] is True
    assert api.get_director(services, who, {})["revision"] != before
    with pytest.raises(MiniAppError):
        api.edit_direction(services, who, body | {"scope": "persistent", "direction": "Stale direction"})
    with pytest.raises(MiniAppError):
        api.edit_direction(services, identity("67890"), body | {"scope": "persistent", "direction": "Other owner"})
    with closing(services.db_factory()) as db:
        assert director_room(db, who.chat_id, body["session_id"])["objective"] == "Follow Mara's search"
        assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0


@pytest.mark.parametrize(
    "values",
    [
        {"scope": "forever", "direction": "bad scope"},
        {"scope": "persistent", "direction": "x" * 4001},
        {"scope": "persistent", "direction": "ok", "revision": "no"},
        {"scope": "persistent", "direction": "ok", "revision": False},
    ],
)
def test_invalid_direction_requests_fail_closed(tmp_path, values):
    services, who, _, body = setup(tmp_path)
    with pytest.raises(MiniAppError):
        api.edit_direction(services, who, body | values)


def test_stale_active_session_is_rejected_before_reassessment(tmp_path, monkeypatch):
    services, who, _, body = setup(tmp_path)
    with closing(services.db_factory()) as db:
        services.session.create(db, who.chat_id, services.config.default_model, session_id="other", title="Other")
    monkeypatch.setattr(
        DirectorService, "reassess", lambda *a, **k: pytest.fail("stale active session called provider")
    )
    with pytest.raises(MiniAppError, match="active session changed"):
        api.reassess(services, who, body | {"confirm": True})


def test_reassess_is_a_bounded_job_using_same_service_and_explicit_actor_scope(tmp_path, monkeypatch):
    services, who, _, body = setup(tmp_path)
    calls = []

    def reassess(self, db, key, chat, session, **kwargs):
        assert not db.in_transaction
        calls.append((chat, session["session_id"], kwargs))
        return SimpleNamespace(result="accepted", reason="Direction updated.")

    monkeypatch.setattr(DirectorService, "reassess", reassess)
    assert api.reassess(services, who, body | {"confirm": True})["accepted"]
    assert calls[0][0:2] == (who.chat_id, body["session_id"])
    assert calls[0][2]["provider_port"] is services.provider
    assert calls[0][2]["persona_service"] is services.persona
    matching = [route for route in api_routes() if route.path == "/director/reassess"]
    assert len(matching) == 1 and matching[0].background_kind == "director_reassess"
    with pytest.raises(MiniAppError):
        api.reassess(services, who, body | {"confirm": False})
    assert len(calls) == 1


def test_closing_room_readable_but_all_mutations_refused(tmp_path, monkeypatch):
    services, who, _, body = setup(tmp_path)
    with closing(services.db_factory()) as db, write_transaction(db):
        db.execute(
            "INSERT INTO ending_state(chat_id,session_id,lifecycle) VALUES(?,?,'closed')",
            (who.chat_id, body["session_id"]),
        )
    data = api.get_director(services, who, {})
    assert not data["mutable"]
    body["revision"] = data["revision"]
    monkeypatch.setattr(DirectorService, "reassess", lambda *a, **k: pytest.fail("closed story called provider"))
    for handler, extra in (
        (api.edit_direction, {"direction": "Reopen", "scope": "persistent"}),
        (api.reassess, {"confirm": True}),
        (api.choose_thread, {"thread_id": "other"}),
        (api.save_controls, {"cadence": "fixed", "interval": 4, "reasoning": 1024}),
    ):
        with pytest.raises(MiniAppError):
            handler(services, who, body | extra)


def test_director_controls_do_not_modify_utility_or_existing_user_preferences(tmp_path):
    services, who, data, body = setup(tmp_path)
    api.save_controls(services, who, body | {"cadence": "fixed", "interval": 7, "reasoning": 8192})
    with closing(services.db_factory()) as db:
        settings = load_session_narrative_settings(db, who.chat_id, body["session_id"])
        assert settings.director_cadence_mode == "fixed" and settings.director_fixed_interval == 7
        assert settings.pov_mode == data["settings"]["pov_mode"]
        assert settings.preset == "custom"
        assert director_reasoning_for_session(db, who.chat_id, body["session_id"]) == 8192
        assert utility_reasoning_for_session(db, who.chat_id, body["session_id"]) == 0
    assert api.get_director(services, who, {})["revision"] != body["revision"]


@pytest.mark.parametrize(
    "values",
    [
        {"interval": 0},
        {"interval": 101},
        {"interval": True},
        {"cadence": "always"},
        {"reasoning": 32001},
        {"reasoning": True},
        {"reasoning": 0.2},
    ],
)
def test_director_control_limits_reject_atomically(tmp_path, values):
    services, who, data, body = setup(tmp_path)
    with pytest.raises(MiniAppError):
        api.save_controls(services, who, body | {"cadence": "fixed", "interval": 6, "reasoning": 0} | values)
    assert api.get_director(services, who, {})["revision"] == data["revision"]


def test_director_model_target_inherits_utility_and_is_independent(tmp_path):
    services, who, _, body = setup(tmp_path)
    services.model_router = ModelRouter(load_catalog=lambda: {"test": {"models": ["model", "utility", "director"]}})
    select_model(services, who, body | {"target": "utility", "model": "test::utility"})
    assert model_catalog(services, who, {})["director"] == "test::utility"
    select_model(services, who, body | {"target": "director", "model": "test::director"})
    catalog = model_catalog(services, who, {})
    assert catalog["director"] == "test::director" and catalog["utility"] == "test::utility"
    assert catalog["story"] == "test::model"
    select_model(services, who, body | {"target": "director", "model": ""})
    assert model_catalog(services, who, {})["director"] == "test::utility"
