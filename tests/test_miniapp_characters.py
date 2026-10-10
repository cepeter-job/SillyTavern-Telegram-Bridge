import base64
import hashlib
import json
import urllib.error
from contextlib import closing
from email.message import Message
from pathlib import Path

import pytest
from miniapp_test_support import card_bytes, identity, make_services
from persisted_state_test_support import seed_character_rank


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
    with closing(s.db_factory()) as db, db:
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


def test_miniapp_optimizer_apply_reranks_character(tmp_path):
    from bridge.character_quality import character_rank
    from bridge.miniapp_characters import apply_proposal, optimize_character
    from bridge.provider_port import ProviderPort

    s, w, p = setup(tmp_path)
    responses = iter(
        [
            json.dumps({"description": "A stronger, internally consistent character description."}),
            "S — the rewritten card is distinctive and consistent.",
        ]
    )
    s.provider = ProviderPort(generate_backend=lambda *a, **k: next(responses))
    target = s.config.character_dir / "Alice.png"
    with closing(s.db_factory()) as db, db:
        seed_character_rank(db, "Alice.png", "B", app_settings=s.config)

    proposal = optimize_character(
        s,
        w,
        {
            **p,
            "filename": "Alice.png",
            "digest": hashlib.sha256(target.read_bytes()).hexdigest(),
        },
    )
    applied = apply_proposal(
        s,
        w,
        {**p, "nonce": proposal["nonce"], "action": "apply", "confirm": True},
    )

    with closing(s.db_factory()) as db, db:
        assert character_rank(db, "Alice.png", app_settings=s.config) == "S"
    assert applied["rank"] == "S"
    assert "Re-ranked S" in applied["message"]


def test_miniapp_optimizer_apply_clears_rank_when_reranking_has_no_result(tmp_path):
    from bridge.character_quality import character_rank
    from bridge.miniapp_characters import apply_proposal, optimize_character
    from bridge.provider_port import ProviderPort

    s, w, p = setup(tmp_path)
    responses = iter(
        [
            json.dumps({"description": "A stronger, internally consistent character description."}),
            "not a valid tier",
        ]
    )
    s.provider = ProviderPort(generate_backend=lambda *a, **k: next(responses))
    target = s.config.character_dir / "Alice.png"
    with closing(s.db_factory()) as db, db:
        seed_character_rank(db, "Alice.png", "A", app_settings=s.config)

    proposal = optimize_character(
        s,
        w,
        {
            **p,
            "filename": "Alice.png",
            "digest": hashlib.sha256(target.read_bytes()).hexdigest(),
        },
    )
    applied = apply_proposal(
        s,
        w,
        {**p, "nonce": proposal["nonce"], "action": "apply", "confirm": True},
    )

    with closing(s.db_factory()) as db, db:
        assert character_rank(db, "Alice.png", app_settings=s.config) == ""
    assert applied["rank"] == ""
    assert "Rank unavailable" in applied["message"]


def test_miniapp_hides_rank_overlay_when_character_has_no_rank():
    source = (Path(__file__).parents[1] / "bridge/miniapp_assets/characters.js").read_text(encoding="utf-8")
    assert "if(!tier)return null" in source
    assert "rank||'Unranked'" not in source


def test_optimizer_provider_failure_returns_safe_miniapp_error(tmp_path):
    from bridge.miniapp_characters import optimize_character
    from bridge.miniapp_errors import MiniAppError
    from bridge.provider_port import ProviderPort

    s, w, p = setup(tmp_path)
    error = urllib.error.HTTPError(
        "https://provider.example/private",
        429,
        "upstream secret body",
        Message(),
        None,
    )

    def fail_provider(*_args, **_kwargs):
        raise error

    s.provider = ProviderPort(fail_provider)
    target = s.config.character_dir / "Alice.png"
    before = target.read_bytes()

    with pytest.raises(MiniAppError) as raised:
        optimize_character(
            s,
            w,
            {
                **p,
                "filename": "Alice.png",
                "digest": hashlib.sha256(before).hexdigest(),
            },
        )

    assert raised.value.status == 429
    assert raised.value.code == "provider_rate_limited"
    assert "rate-limited (HTTP 429)" in str(raised.value)
    assert "upstream secret body" not in str(raised.value)
    assert "provider.example" not in str(raised.value)
    assert target.read_bytes() == before


@pytest.mark.parametrize("filename", ["*.png", "x?.png", "a[0].png", "bad\\name.png", "../x.png", ".hidden.png"])
def test_upload_rejects_path_and_glob_metacharacters(tmp_path, filename):
    from bridge.miniapp_characters import upload_character

    s, w, p = setup(tmp_path)
    with pytest.raises(ValueError):
        upload_character(s, w, {**p, "filename": filename, "data": base64.b64encode(card_bytes()).decode()})


def test_character_backup_list_restore_and_stale_guard(tmp_path):
    from bridge.miniapp_characters import character_backups, restore_character_backup
    from bridge.miniapp_errors import MiniAppError
    from bridge.native_imports import verify_character_card_backup

    s, w, p = setup(tmp_path)
    target = s.config.character_dir / "Alice.png"
    original = target.read_bytes()
    verify_character_card_backup(target, original, app_settings=s.config)
    target.write_bytes(card_bytes("Alice changed"))

    backups = character_backups(s, w, {})["backups"]
    alice = next(item for item in backups if item["filename"] == "Alice.png")
    assert alice["installed"] is True
    assert alice["digest"]
    assert alice["backup_digest"] == hashlib.sha256(original).hexdigest()
    assert alice["matches_installed"] is False

    with pytest.raises(MiniAppError) as missing_confirm:
        restore_character_backup(
            s,
            w,
            {
                **p,
                "filename": "Alice.png",
                "digest": alice["digest"],
                "backup_digest": alice["backup_digest"],
            },
        )
    assert missing_confirm.value.code == "confirmation"

    with pytest.raises(MiniAppError) as stale:
        restore_character_backup(
            s,
            w,
            {
                **p,
                "filename": "Alice.png",
                "digest": "0" * 64,
                "backup_digest": alice["backup_digest"],
                "confirm": True,
            },
        )
    assert stale.value.code == "stale"

    with pytest.raises(MiniAppError) as changed_backup:
        restore_character_backup(
            s,
            w,
            {
                **p,
                "filename": "Alice.png",
                "digest": alice["digest"],
                "backup_digest": "0" * 64,
                "confirm": True,
            },
        )
    assert changed_backup.value.code == "stale"

    restored = restore_character_backup(
        s,
        w,
        {
            **p,
            "filename": "Alice.png",
            "digest": alice["digest"],
            "backup_digest": alice["backup_digest"],
            "confirm": True,
        },
    )
    assert restored["restored"] is True
    assert "restoring again will undo this change" in restored["message"]
    assert target.read_bytes() == original


def test_deleted_character_can_be_restored_from_miniapp_backup(tmp_path):
    from bridge.miniapp_characters import character_backups, restore_character_backup
    from bridge.native_imports import verify_character_card_backup

    s, w, p = setup(tmp_path)
    target = s.config.character_dir / "Alice.png"
    raw = target.read_bytes()
    verify_character_card_backup(target, raw, app_settings=s.config)
    target.unlink()

    alice = next(item for item in character_backups(s, w, {})["backups"] if item["filename"] == "Alice.png")
    assert alice["installed"] is False
    assert alice["digest"] == ""
    assert alice["backup_digest"] == hashlib.sha256(raw).hexdigest()
    assert alice["matches_installed"] is False

    restore_character_backup(
        s,
        w,
        {
            **p,
            "filename": "Alice.png",
            "digest": "",
            "backup_digest": alice["backup_digest"],
            "confirm": True,
        },
    )
    assert target.read_bytes() == raw


def test_character_restore_rejects_backup_identical_to_installed_card(tmp_path):
    from bridge.miniapp_characters import character_backups, restore_character_backup
    from bridge.miniapp_errors import MiniAppError
    from bridge.native_imports import verify_character_card_backup

    s, w, p = setup(tmp_path)
    target = s.config.character_dir / "Alice.png"
    raw = target.read_bytes()
    verify_character_card_backup(target, raw, app_settings=s.config)

    alice = next(item for item in character_backups(s, w, {})["backups"] if item["filename"] == "Alice.png")
    assert alice["matches_installed"] is True

    with pytest.raises(MiniAppError) as unchanged:
        restore_character_backup(
            s,
            w,
            {
                **p,
                "filename": "Alice.png",
                "digest": alice["digest"],
                "backup_digest": alice["backup_digest"],
                "confirm": True,
            },
        )
    assert unchanged.value.code == "unchanged"
    assert target.read_bytes() == raw


@pytest.mark.parametrize("preset", ["observer", "custom"])
def test_miniapp_character_setup_preserves_personal_style_prefill_only_for_new_session(tmp_path, preset):
    from bridge.miniapp_characters import select_character
    from bridge.narrative_settings import (
        load_session_narrative_settings,
        normalize_narrative_settings,
        preset_narrative_settings,
        save_user_narrative_default,
    )

    services, who, params = setup(tmp_path)
    preferred = (
        normalize_narrative_settings({"preset": "custom", "offscreen_policy": "free"})
        if preset == "custom"
        else preset_narrative_settings(preset)
    )
    with closing(services.db_factory()) as db, db:
        original = load_session_narrative_settings(db, who.chat_id, params["session_id"])
        save_user_narrative_default(db, who.user_id, preferred)
    result = select_character(services, who, {**params, "filename": "Alice.png", "confirm": True})
    with closing(services.db_factory()) as db, db:
        assert load_session_narrative_settings(db, who.chat_id, result["session"]["session_id"]) == preferred
        assert load_session_narrative_settings(db, who.chat_id, params["session_id"]) == original
