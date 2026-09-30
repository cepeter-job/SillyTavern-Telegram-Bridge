import hashlib

import pytest
from test_character_mutation_safety import _card_png
from test_character_mutation_safety import card_context as card_context

import bridge.cards as cards
import bridge.character_callbacks as character_callbacks
from bridge.native_imports import (
    character_restore_targets,
    restore_character_card_backup,
    verify_character_card_backup,
)


def test_restore_character_backup_replaces_card_and_preserves_current_revision(card_context):
    _db, ctx, _ = card_context
    target = ctx.app_settings.character_dir / "Alice.png"
    original = _card_png("Alice", "original backup")
    current = _card_png("Alice", "current revision")
    target.write_bytes(original)
    verify_character_card_backup(target, original, app_settings=ctx.app_settings)
    target.write_bytes(current)

    restored = restore_character_card_backup(
        "Alice.png",
        expected_digest=hashlib.sha256(current).hexdigest(),
        app_settings=ctx.app_settings,
    )

    assert restored.name == "Alice.png"
    assert target.read_bytes() == original
    assert (ctx.app_settings.character_backup_dir / "Alice.png").read_bytes() == current


def test_restore_character_backup_can_recreate_deleted_character(card_context):
    _db, ctx, _ = card_context
    target = ctx.app_settings.character_dir / "Alice.png"
    original = _card_png("Alice", "deleted original")
    target.write_bytes(original)
    verify_character_card_backup(target, original, app_settings=ctx.app_settings)
    target.unlink()

    restored = restore_character_card_backup(
        "Alice.png",
        expected_digest="",
        app_settings=ctx.app_settings,
    )

    assert restored == target
    assert target.read_bytes() == original


def test_restore_rejects_stale_or_unsafe_requests(card_context):
    _db, ctx, _ = card_context
    target = ctx.app_settings.character_dir / "Alice.png"
    original = _card_png("Alice", "backup")
    current = _card_png("Alice", "current")
    target.write_bytes(original)
    verify_character_card_backup(target, original, app_settings=ctx.app_settings)
    target.write_bytes(current)

    with pytest.raises(ValueError, match="changed"):
        restore_character_card_backup(
            "Alice.png",
            expected_digest=hashlib.sha256(b"stale").hexdigest(),
            app_settings=ctx.app_settings,
        )
    with pytest.raises(ValueError, match="filename"):
        restore_character_card_backup(
            "../Alice.png",
            expected_digest="",
            app_settings=ctx.app_settings,
        )
    assert target.read_bytes() == current


def test_restore_targets_include_deleted_character(card_context):
    _db, ctx, _ = card_context
    target = ctx.app_settings.character_dir / "Alice.png"
    raw = _card_png("Alice", "backup")
    target.write_bytes(raw)
    verify_character_card_backup(target, raw, app_settings=ctx.app_settings)
    target.unlink()

    assert "Alice.png" in character_restore_targets(app_settings=ctx.app_settings)


def test_character_menu_exposes_restore_action(card_context, monkeypatch):
    _db, ctx, _ = card_context
    (ctx.app_settings.character_dir / "Alice.png").write_bytes(_card_png("Alice", "installed"))
    delivered = []
    monkeypatch.setattr(
        cards,
        "send_panel_message",
        lambda _token, _chat, text, markup, *a, **k: delivered.append((text, markup)),
    )

    cards.send_character_menu(
        "token",
        "chat",
        "Alice.png",
        request_context=ctx,
    )

    callbacks = {
        button["callback_data"]
        for row in delivered[-1][1]["inline_keyboard"]
        for button in row
    }
    assert "character:restore" in callbacks


def test_character_restore_callback_opens_restore_menu(card_context, monkeypatch):
    db, ctx, _ = card_context
    opened = []
    monkeypatch.setattr(
        character_callbacks,
        "send_character_restore_menu",
        lambda *a, **k: opened.append((a, k)),
    )

    handled = character_callbacks.handle_character_callback(
        db,
        "token",
        {"id": "cb", "message": {"message_id": 55}},
        lambda *_a: None,
        "character:restore",
        "chat",
        {"message_id": 55},
        {"session_id": ctx.session_id, "character_file": "Alice.png"},
        ctx.session_id,
        None,
        group_service=object(),
        provider_port=object(),
        request_context=ctx,
    )

    assert handled is True
    assert len(opened) == 1
