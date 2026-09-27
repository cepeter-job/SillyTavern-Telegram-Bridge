import base64
import hashlib
import json

import pytest
from miniapp_test_support import card_bytes, identity, make_services


def setup(tmp_path):
    from bridge.miniapp_context import current_session

    services = make_services(tmp_path)
    who = identity()
    session = current_session(services, who, {})["session"]
    return services, who, {"session_id": session["session_id"]}


def test_catalog_and_portrait_are_safe_and_real(tmp_path):
    from bridge.miniapp_characters import character_info, character_portrait, list_characters

    s, w, _p = setup(tmp_path)
    catalog = list_characters(s, w, {})
    assert {x["filename"] for x in catalog["characters"]} == {"Alice.png", "Default.png"}
    info = character_info(s, w, {"filename": "Alice.png"})
    assert info["fields"]["name"] == "Alice"
    assert str(tmp_path) not in json.dumps(info)
    assert character_portrait(s, w, {"filename": "Alice.png"}).content.startswith(b"\x89PNG")
    with pytest.raises(ValueError):
        character_info(s, w, {"filename": "../private.png"})


def test_character_selection_creates_new_session_not_rewrites_history(tmp_path):
    from bridge.miniapp_characters import select_character

    s, w, p = setup(tmp_path)
    result = select_character(s, w, {**p, "filename": "Alice.png", "confirm": True})
    assert result["session"]["character_file"] == "Alice.png"
    assert result["session"]["session_id"] != p["session_id"]
    with s.db_factory() as db:
        assert s.session.load(db, w.chat_id, p["session_id"], s.config.default_model)["character_file"] == "Default.png"
    with pytest.raises(ValueError):
        select_character(s, w, {**p, "filename": "Alice.png", "confirm": True})


def test_delete_checks_confirmation_default_references_and_digest(tmp_path):
    from bridge.miniapp_characters import delete_character

    s, w, p = setup(tmp_path)
    target = s.config.character_dir / "Alice.png"
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    for fields in [
        {"filename": "Default.png", "confirm": True, "digest": digest},
        {"filename": "Alice.png", "digest": digest},
        {"filename": "Alice.png", "confirm": True, "digest": "0" * 64},
    ]:
        with pytest.raises(ValueError):
            delete_character(s, w, {**p, **fields})
    assert target.exists()
    delete_character(s, w, {**p, "filename": "Alice.png", "confirm": True, "digest": digest})
    assert not target.exists()
    assert list(s.config.character_backup_dir.glob("Alice*.png"))


def test_upload_preview_does_not_overwrite_card(tmp_path):
    from bridge.miniapp_characters import apply_proposal, upload_character

    s, w, p = setup(tmp_path)
    raw = card_bytes("Revised")
    target = s.config.character_dir / "Alice.png"
    original = target.read_bytes()
    result = upload_character(s, w, {**p, "filename": "Alice.png", "data": base64.b64encode(raw).decode()})
    assert target.read_bytes() == original
    assert result["nonce"]
    apply_proposal(s, w, {**p, "nonce": result["nonce"], "action": "overwrite", "confirm": True})
    assert target.read_bytes() == raw
    with pytest.raises(ValueError):
        apply_proposal(s, w, {**p, "nonce": result["nonce"], "action": "overwrite", "confirm": True})


def test_optimizer_proposal_retains_original_and_is_actor_bound(tmp_path):
    from bridge.miniapp_characters import apply_proposal, optimize_character
    from bridge.provider_port import ProviderPort

    s, w, p = setup(tmp_path)
    s.provider = ProviderPort(
        generate_backend=lambda *a, **k: json.dumps(
            {"description": "A thoughtful companion with clear motivations and consistent habits."}
        )
    )
    target = s.config.character_dir / "Alice.png"
    before = target.read_bytes()
    result = optimize_character(
        s,
        w,
        {
            **p,
            "filename": "Alice.png",
            "suggestion": "Clarify motivation.",
            "digest": hashlib.sha256(before).hexdigest(),
        },
    )
    assert target.read_bytes() == before
    assert result["fields"]["description"] != result["original"]["description"]
    with pytest.raises(ValueError):
        apply_proposal(s, identity("67890"), {**p, "nonce": result["nonce"], "action": "apply", "confirm": True})
    apply_proposal(s, w, {**p, "nonce": result["nonce"], "action": "apply", "confirm": True})
    assert target.read_bytes() != before


@pytest.mark.parametrize("filename", ["*.png", "x?.png", "a[0].png", "bad\\name.png", "../x.png", ".hidden.png"])
def test_upload_rejects_path_and_glob_metacharacters(tmp_path, filename):
    from bridge.miniapp_characters import upload_character

    s, w, p = setup(tmp_path)
    with pytest.raises(ValueError):
        upload_character(s, w, {**p, "filename": filename, "data": base64.b64encode(card_bytes()).decode()})
