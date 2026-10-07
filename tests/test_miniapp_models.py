import json
import math

import pytest
from miniapp_test_support import identity, make_services


def setup(tmp_path):
    from bridge.miniapp_context import current_session
    from bridge.model_router import ModelRouter

    services = make_services(tmp_path)
    services.model_router = ModelRouter(
        load_catalog=lambda: {
            "test": {"models": ["model", "other"], "api_key": "never-expose", "api_endpoint": "https://private.example"}
        }
    )
    who = identity()
    session = current_session(services, who, {})["session"]
    return services, who, {"session_id": session["session_id"]}


def test_catalog_exposes_only_sanitized_model_ids(tmp_path):
    from bridge.miniapp_models import model_catalog

    s, w, _p = setup(tmp_path)
    data = model_catalog(s, w, {})
    assert data["models"] == [
        {"id": "test::model", "provider": "test", "name": "model"},
        {"id": "test::other", "provider": "test", "name": "other"},
    ]
    assert "never-expose" not in json.dumps(data) and "private.example" not in json.dumps(data)


def test_story_and_utility_selection_are_session_scoped(tmp_path):
    from bridge.miniapp_models import model_catalog, select_model

    s, w, p = setup(tmp_path)
    select_model(s, w, {**p, "target": "story", "model": "test::other"})
    assert model_catalog(s, w, {})["story"] == "test::other"
    select_model(s, w, {**p, "target": "utility", "model": "test::model"})
    assert model_catalog(s, w, {})["utility"] == "test::model"
    with pytest.raises(ValueError):
        select_model(s, w, {**p, "target": "story", "model": "test::missing"})
    with pytest.raises(ValueError):
        select_model(s, w, {**p, "session_id": "foreign", "target": "story", "model": "test::model"})


def test_catalog_distinguishes_inherited_and_explicit_role_selections(tmp_path):
    from bridge.miniapp_models import model_catalog, select_model

    services, who, body = setup(tmp_path)
    initial = model_catalog(services, who, {})
    assert initial["configured"] == {"story": "test::model", "utility": "", "director": ""}
    assert initial["utility"] == initial["director"] == initial["story"]
    select_model(services, who, body | {"target": "utility", "model": "test::model"})
    explicit = model_catalog(services, who, {"q": "no-match"})
    assert explicit["models"] == []
    assert explicit["configured"]["utility"] == "test::model"
    assert explicit["configured"]["director"] == ""
    select_model(services, who, body | {"target": "utility", "model": ""})
    assert model_catalog(services, who, {})["configured"]["utility"] == ""
    other = model_catalog(services, identity("67890"), {})
    assert other["configured"]["utility"] == other["configured"]["director"] == ""


@pytest.mark.parametrize(
    "setting,value",
    [
        ("temperature", -1),
        ("temperature", 3),
        ("top_p", 1.1),
        ("max_tokens", 0),
        ("max_tokens", 2.5),
        ("max_tokens", True),
        ("frequency_penalty", math.nan),
        ("top_p", math.inf),
        ("temperature", None),
        ("unexpected", 1),
        ("stop_sequences", ["not-a-string"]),
        ("reasoning_budget", -2),
    ],
)
def test_bad_generation_values_leave_all_settings_unchanged(tmp_path, setting, value):
    from bridge.miniapp_models import generation, save_generation

    s, w, p = setup(tmp_path)
    before = generation(s, w, {})["settings"]
    with pytest.raises(ValueError):
        save_generation(s, w, {**p, "settings": {setting: value, "presence_penalty": 0.5}})
    assert generation(s, w, {})["settings"] == before


def test_generation_presets_are_validated_and_user_scoped(tmp_path):
    from bridge.miniapp_models import apply_preset, delete_preset, generation, presets, save_generation, save_preset

    s, w, p = setup(tmp_path)
    save_generation(
        s, w, {**p, "settings": {"temperature": 0.4, "top_p": 0.9, "max_tokens": 2048, "stop_sequences": "STOP"}}
    )
    save_preset(s, w, {**p, "name": "Test_preset"})
    save_generation(s, w, {**p, "settings": {"temperature": 0.8}})
    apply_preset(s, w, {**p, "name": "Test_preset"})
    assert generation(s, w, {})["settings"]["temperature"] == 0.4
    assert presets(s, identity("67890"), {})["presets"] == []
    with pytest.raises(ValueError):
        delete_preset(s, w, {**p, "name": "Test_preset"})
    delete_preset(s, w, {**p, "name": "Test_preset", "confirm": True})
    assert presets(s, w, {})["presets"] == []
