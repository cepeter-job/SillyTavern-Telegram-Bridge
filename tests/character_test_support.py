"""Shared native-character test fixtures and card builders."""

from __future__ import annotations

import base64
import json
import struct
import zlib
from dataclasses import replace

import pytest

from bridge import native_imports as imports
from bridge.request_types import RequestContext
from bridge.settings import load_app_settings
from bridge.sqlite_store import db_connect


def card_png(name: str, description: str = "") -> bytes:
    card = {
        "name": name,
        "description": description,
        "personality": "",
        "scenario": "",
        "first_mes": "",
        "mes_example": "",
    }
    encoded = base64.b64encode(json.dumps(card).encode("utf-8"))
    chunk_data = b"chara\x00" + encoded
    chunk = (
        struct.pack(">I", len(chunk_data))
        + b"tEXt"
        + chunk_data
        + struct.pack(">I", zlib.crc32(b"tEXt" + chunk_data) & 0xFFFFFFFF)
    )
    return b"\x89PNG\r\n\x1a\n" + chunk


@pytest.fixture
def card_context(tmp_path, monkeypatch):
    settings = replace(
        load_app_settings({}, home=tmp_path),
        character_dir=tmp_path / "characters",
        character_backup_dir=tmp_path / "backups",
    )
    settings.character_dir.mkdir()
    db = db_connect(app_settings=settings)
    ctx = RequestContext(db, "session", "actor", app_settings=settings)
    panels = []
    monkeypatch.setattr(imports, "send_text", lambda *a, **k: [])
    monkeypatch.setattr(
        imports, "send_panel_request", lambda _token, _method, payload, **kwargs: panels.append(payload)
    )
    try:
        yield db, ctx, panels
    finally:
        db.close()


def upload(card_context, raw):
    db, ctx, panels = card_context
    imports.import_character_card(
        db, "token", "chat", "Alice.png", raw, app_settings=ctx.app_settings, request_context=ctx
    )
    return panels


def proposal_nonce(card_context):
    callback = card_context[2][-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    return callback.rsplit(":", 1)[1]


def apply_proposal(card_context, nonce, action="overwrite", *, context=None, chat_id="chat"):
    db, ctx, _ = card_context
    assert hasattr(imports, "apply_character_proposal"), "missing nonce-bound character mutation boundary"
    return imports.apply_character_proposal(db, chat_id, nonce, action, request_context=context or ctx)


def prepare_replacement(card_context):
    original, updated = card_png("Alice", "original"), card_png("Alice", "updated")
    upload(card_context, original)
    upload(card_context, updated)
    return original, updated, proposal_nonce(card_context)
