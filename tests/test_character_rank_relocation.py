from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from character_test_support import card_context as card_context
from character_test_support import card_png as _card_png
from character_test_support import upload
from persisted_state_test_support import seed_character_rank

from bridge import character_quality as quality
from bridge.metadata import get_meta, set_meta


def _legacy_signature(path: Path) -> list[str | int]:
    stat = path.stat()
    return [str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ino]


def test_rank_survives_character_directory_relocation(card_context, tmp_path):
    db, ctx, _ = card_context
    raw = _card_png("Alice", "original")
    upload(card_context, raw)
    seed_character_rank(db, "Alice.png", "S", app_settings=ctx.app_settings)

    moved_dir = tmp_path / "moved-characters"
    moved_dir.mkdir()
    (moved_dir / "Alice.png").write_bytes(raw)
    moved_settings = replace(ctx.app_settings, character_dir=moved_dir)

    assert quality.character_rank(db, "Alice.png", app_settings=moved_settings) == "S"


def test_new_rank_state_uses_content_digest(card_context):
    db, ctx, _ = card_context
    upload(card_context, _card_png("Alice", "original"))
    seed_character_rank(db, "Alice.png", "A", app_settings=ctx.app_settings)

    state = json.loads(get_meta(db, quality.RANK_META_PREFIX + "Alice.png", "{}"))

    assert state["file_signature"][0] == "sha256"
    assert len(state["file_signature"][1]) == 64


def test_legacy_rank_relocates_only_when_original_file_still_proves_identity(card_context, tmp_path):
    db, ctx, _ = card_context
    raw = _card_png("Alice", "original")
    upload(card_context, raw)
    source = ctx.app_settings.character_dir / "Alice.png"
    set_meta(
        db,
        quality.RANK_META_PREFIX + "Alice.png",
        json.dumps({"rank": "S", "file_signature": _legacy_signature(source)}),
    )

    moved_dir = tmp_path / "moved-characters"
    moved_dir.mkdir()
    (moved_dir / "Alice.png").write_bytes(raw)
    moved_settings = replace(ctx.app_settings, character_dir=moved_dir)

    assert quality.character_rank(db, "Alice.png", app_settings=moved_settings) == "S"
    state = json.loads(get_meta(db, quality.RANK_META_PREFIX + "Alice.png", "{}"))
    assert state["file_signature"][0] == "sha256"


def test_legacy_rank_stays_invalid_when_original_file_cannot_prove_identity(card_context, tmp_path):
    db, ctx, _ = card_context
    raw = _card_png("Alice", "original")
    upload(card_context, raw)
    source = ctx.app_settings.character_dir / "Alice.png"
    legacy = _legacy_signature(source)
    set_meta(
        db,
        quality.RANK_META_PREFIX + "Alice.png",
        json.dumps({"rank": "S", "file_signature": legacy}),
    )

    moved_dir = tmp_path / "moved-characters"
    moved_dir.mkdir()
    (moved_dir / "Alice.png").write_bytes(raw)
    source.unlink()
    moved_settings = replace(ctx.app_settings, character_dir=moved_dir)

    assert quality.character_rank(db, "Alice.png", app_settings=moved_settings) == ""
