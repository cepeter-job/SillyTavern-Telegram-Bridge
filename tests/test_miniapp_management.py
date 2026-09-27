import pytest
from miniapp_test_support import identity, make_services


def setup(tmp_path):
    from application_test_setup import make_native_test_persona_service

    from bridge.miniapp_context import current_session

    s = make_services(tmp_path)
    s.persona = make_native_test_persona_service(app_settings=s.config)
    w = identity()
    session = current_session(s, w, {})["session"]
    return s, w, {"session_id": session["session_id"]}


def test_session_lifecycle_and_private_ownership(tmp_path):
    from bridge.miniapp_sessions import create_session, delete_session, edit_session, select_session, sessions

    s, w, p = setup(tmp_path)
    result = create_session(s, w, {**p, "title": "Evening", "operation_id": "new-test-session"})
    new = result["session"]["session_id"]
    assert result["session"]["title"] == "Evening"
    edit_session(s, w, {"session_id": new, "target_id": new, "title": "Renamed"})
    assert sessions(s, w, {})["sessions"][0]["title"] == "Renamed"
    with pytest.raises(ValueError):
        select_session(s, identity("67890"), {"session_id": "default", "target_id": new})
    with pytest.raises(ValueError):
        delete_session(s, w, {"session_id": new, "target_id": new, "confirm": True})
    select_session(s, w, {"session_id": new, "target_id": "default"})
    delete_session(s, w, {"session_id": "default", "target_id": new, "confirm": True})
    assert len(sessions(s, w, {})["sessions"]) == 1


def test_personas_reuse_native_service_and_revision_guards(tmp_path):
    from bridge.miniapp_sessions import create_persona, delete_persona, edit_persona, personas, select_persona

    s, w, p = setup(tmp_path)
    result = create_persona(
        s, w, {**p, "logical_id": "reader", "name": "Reader", "description": "An inquisitive visitor."}
    )
    persona_id = result["id"]
    saved = next(x for x in personas(s, w, {})["personas"] if x["id"] == persona_id)
    with pytest.raises(ValueError):
        edit_persona(
            s, w, {**p, "persona_id": persona_id, "name": "Changed", "description": "New text", "digest": "0" * 64}
        )
    edit_persona(
        s, w, {**p, "persona_id": persona_id, "name": "Changed", "description": "New text", "digest": saved["digest"]}
    )
    changed = next(x for x in personas(s, w, {})["personas"] if x["id"] == persona_id)
    with pytest.raises(ValueError):
        delete_persona(s, w, {**p, "persona_id": persona_id, "digest": changed["digest"], "confirm": True})
    select_persona(s, w, {**p, "persona_id": ""})
    delete_persona(s, w, {**p, "persona_id": persona_id, "digest": changed["digest"], "confirm": True})
    assert personas(s, w, {})["personas"] == []


def test_world_editor_backup_revision_selection_and_delete(tmp_path):
    from bridge.miniapp_worlds import create_world, delete_world, edit_world, select_worlds, world_info, worlds

    s, w, p = setup(tmp_path)
    initial = {"entries": {"1": {"key": ["garden"], "content": "A quiet garden."}}}
    create_world(s, w, {**p, "filename": "Garden.json", "document": initial})
    info = world_info(s, w, {"filename": "Garden.json"})
    revised = {"entries": {"1": {"key": ["garden"], "content": "A shaded garden."}}}
    with pytest.raises(ValueError):
        edit_world(s, w, {**p, "filename": "Garden.json", "document": revised, "digest": "0" * 64, "confirm": True})
    edit_world(s, w, {**p, "filename": "Garden.json", "document": revised, "digest": info["digest"], "confirm": True})
    assert world_info(s, w, {"filename": "Garden.json"})["document"] == revised
    select_worlds(s, w, {**p, "worlds": ["Garden.json"]})
    current = world_info(s, w, {"filename": "Garden.json"})
    with pytest.raises(ValueError):
        delete_world(s, w, {**p, "filename": "Garden.json", "digest": current["digest"], "confirm": True})
    select_worlds(s, w, {**p, "worlds": []})
    delete_world(s, w, {**p, "filename": "Garden.json", "digest": current["digest"], "confirm": True})
    assert worlds(s, w, {})["worlds"] == []
    assert list((s.config.bridge_home / "backups/worlds").glob("Garden.*.json"))


@pytest.mark.parametrize("filename", ["../x.json", "*.json", "bad\\name.json", ".hidden.json"])
def test_world_creation_never_accepts_unsafe_filenames(tmp_path, filename):
    from bridge.miniapp_worlds import create_world

    s, w, p = setup(tmp_path)
    with pytest.raises(ValueError):
        create_world(s, w, {**p, "filename": filename, "document": {"entries": {}}})
    assert list(s.config.world_dir.iterdir()) == []
