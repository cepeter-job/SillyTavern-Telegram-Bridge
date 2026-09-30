import sqlite3
import time

from application_test_setup import (
    make_test_delivery_port,
    make_test_memory_service,
    make_test_persona_service,
    make_test_provider_port,
    make_test_rag_service,
    make_test_request_context,
)
from settings_test_support import SettingsBuilder

import bridge.callbacks as callbacks
import bridge.command_panels as command_panels
import bridge.npc_callbacks as npc_callbacks
import bridge.npc_panels as npc_panels
from bridge.npc_repository import find_npc_exact, load_npc_fields
from bridge.npc_service import NpcService
from bridge.npc_types import NpcExtractionGroup, NpcOperation
from bridge.schema import initialize_database_schema


def _db():
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys=ON")
    initialize_database_schema(db)
    now = time.time()
    db.execute(
        """
        INSERT INTO sessions(
            chat_id,session_id,title,character_file,model_id,persona_id,world_file,
            author_note,system_prompt,response_language,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        ("chat", "s1", "Session", "mira.png", "story::main", "", "", "", "", "auto", now, now),
    )
    db.commit()
    return db


def _session():
    return {
        "session_id": "s1",
        "character_file": "mira.png",
        "model_id": "story::main",
        "persona_id": "",
    }


def _fields():
    return {"name": "Mira"}


def _apply(service, db, rowid, value, *, field="relationship", visibility="shared", known_by=()):
    result = service.apply_group(
        db,
        "chat",
        "s1",
        NpcExtractionGroup(
            "Maya Torres",
            ("Maya",),
            (
                NpcOperation(
                    field,
                    "set",
                    value,
                    "mutable",
                    visibility,
                    tuple(known_by),
                ),
            ),
        ),
        source_rowid=rowid,
        primary_name="Mira",
        user_name="User",
    )
    assert result.applied == 1
    return result.npc_id


def test_npc_menu_lists_session_entities(monkeypatch):
    db = _db()
    service = NpcService()
    delivered = []
    try:
        npc_id = _apply(service, db, 10, "cautious")
        monkeypatch.setattr(
            npc_panels,
            "send_panel_message",
            lambda _token, _chat, text, markup, *a, **k: delivered.append((text, markup)),
        )
        npc_panels.send_npc_menu(
            "token",
            "chat",
            db,
            _session(),
            npc_service=service,
            request_context=make_test_request_context(db, "s1", "actor", app_settings=SettingsBuilder().build()),
        )

        text, markup = delivered[-1]
        assert "NPC Bank" in text
        buttons = [button for row in markup["inline_keyboard"] for button in row]
        maya = next(button for button in buttons if "Maya Torres" in button["text"])
        assert maya["callback_data"] == f"npc:view:{npc_id}"
        assert any(button["callback_data"] == "npc:refresh" for button in buttons)
    finally:
        db.close()


def test_npc_detail_hides_restricted_fields_from_wrong_active_character(monkeypatch):
    db = _db()
    service = NpcService()
    delivered = []
    try:
        npc_id = _apply(service, db, 10, "Archivist", field="role")
        _apply(
            service,
            db,
            11,
            ["Vault code 7741"],
            field="secrets",
            visibility="restricted",
            known_by=("Maya Torres",),
        )
        monkeypatch.setattr(
            npc_panels,
            "send_panel_message",
            lambda _token, _chat, text, markup, *a, **k: delivered.append((text, markup)),
        )

        npc_panels.send_npc_detail(
            "token",
            "chat",
            db,
            _session(),
            _fields(),
            npc_id,
            npc_service=service,
            request_context=make_test_request_context(
                db, "s1", "actor", app_settings=SettingsBuilder().build()
            ),
        )

        text, markup = delivered[-1]
        assert "Maya Torres" in text
        assert "Archivist" in text
        assert "Vault code 7741" not in text
        callbacks_seen = {button["callback_data"] for row in markup["inline_keyboard"] for button in row}
        assert f"npc:history:{npc_id}" in callbacks_seen
    finally:
        db.close()


def test_undo_latest_npc_field_change_restores_previous_value():
    db = _db()
    service = NpcService()
    try:
        npc_id = _apply(service, db, 10, "cautious")
        _apply(service, db, 20, "hostile")

        assert service.undo_latest_field_change(db, "chat", "s1", npc_id, "relationship") is True

        entity = find_npc_exact(db, "chat", "s1", "maya torres")
        assert entity is not None
        assert load_npc_fields(db, entity.npc_id)["relationship"].value == "cautious"
    finally:
        db.close()


def test_npc_refresh_callback_runs_extractor_and_redraws(monkeypatch):
    db = _db()
    service = NpcService()
    answers = []
    redrawn = []
    try:
        monkeypatch.setattr(npc_callbacks, "refresh_npc_state_now", lambda *a, **k: 2)
        monkeypatch.setattr(
            npc_callbacks,
            "send_npc_menu",
            lambda *a, **k: redrawn.append((a, k)),
        )

        handled = npc_callbacks.handle_npc_callback(
            db,
            "token",
            {"id": "cb"},
            lambda _token, _id, text: answers.append(text),
            "npc:refresh",
            "chat",
            {"message_id": 55},
            _session(),
            _fields(),
            npc_service=service,
            provider_port=make_test_provider_port(),
            request_context=make_test_request_context(
                db, "s1", "actor", app_settings=SettingsBuilder().build()
            ),
        )

        assert handled is True
        assert answers == ["NPC Bank refreshed: 2 updates"]
        assert len(redrawn) == 1
    finally:
        db.close()


def test_npc_callbacks_are_session_scoped():
    assert callbacks.is_session_scoped_panel_callback("npc:view:12") is True


def test_npc_command_opens_panel(monkeypatch):
    db = _db()
    service = NpcService()
    opened = []
    try:
        monkeypatch.setattr(
            command_panels,
            "send_npc_menu",
            lambda *a, **k: opened.append((a, k)),
        )
        handled = command_panels._handle_memory_media(
            db,
            "token",
            "key",
            "chat",
            "/npc",
            "/npc",
            _session(),
            _fields(),
            None,
            request_context=make_test_request_context(
                db, "s1", "actor", app_settings=SettingsBuilder().build()
            ),
            delivery_port=make_test_delivery_port(),
            group_service=object(),
            memory_service=make_test_memory_service(),
            npc_service=service,
            persona_service=make_test_persona_service(),
            provider_port=make_test_provider_port(),
            sync_service=object(),
            rag_service=make_test_rag_service(),
        )

        assert handled is True
        assert len(opened) == 1
    finally:
        db.close()
