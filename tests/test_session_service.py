"""Session ownership is application state, not Telegram transport."""

from __future__ import annotations

import ast
import importlib
import tempfile
from dataclasses import MISSING
from pathlib import Path

import pytest
from application_test_setup import ensure_application_extensions, make_test_conversation_service
from settings_test_support import SettingsTestCase

import bridge.memory_curator as _m_memory_curator
import bridge.message_commands as _m_message_commands
import bridge.session_naming as _m_session_naming
from bridge.composition import BridgeServices
from bridge.main import _build_startup_services
from bridge.model_router import ModelRouter
from bridge.settings import load_app_settings
from bridge.sqlite_store import db_connect, write_transaction

ROOT = Path(__file__).parents[1]


def test_session_service_is_required_by_composition():
    assert "session" in BridgeServices.__dataclass_fields__, "missing required SessionService"
    assert BridgeServices.__dataclass_fields__["session"].default is MISSING


def test_telegram_no_longer_owns_session_lifecycle_or_native_imports():
    tree = ast.parse((ROOT / "bridge/telegram.py").read_text())
    owned = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert not owned & {
        "load_session",
        "ensure_session",
        "update_session",
        "create_session",
        "list_sessions",
        "delete_session_data",
        "import_character_card",
        "import_world_info_document",
        "import_telegram_document",
        "verify_character_card_backup",
        "send_session_delete_menu",
        "send_session_delete_confirm",
    }


def test_canonical_session_repository_never_owns_transactions():
    path = ROOT / "bridge/session_repository.py"
    assert path.is_file(), "session SQL must have a canonical owner"
    tree = ast.parse(path.read_text())
    calls = [n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
    assert not {"commit", "rollback"} & set(calls)
    assert "bridge.telegram" not in path.read_text()


def compose(tmp_path, character="Fixture.png"):
    settings = load_app_settings(
        {"SILLYTAVERN_DEFAULT_CHARACTER": character, "SILLYTAVERN_MODEL": "provider::model"}, home=tmp_path
    )
    services = _build_startup_services(settings, model_router=ModelRouter(load_catalog=lambda: {}))
    assert hasattr(services, "session"), "session lifecycle must be composed explicitly"
    return services, db_connect(app_settings=settings)


def test_composed_session_service_creates_selects_updates_and_lists_without_transport(tmp_path):
    services, db = compose(tmp_path)
    try:
        default = services.session.ensure(db, "chat", "provider::model")
        assert default["session_id"] == "default"
        created = services.session.create(db, "chat", "provider::model", session_id="new", title="New session")
        assert created["character_file"] == "Fixture.png"
        assert services.session.ensure(db, "chat", "provider::model")["session_id"] == "new"
        services.session.update(db, "chat", "new", title="Renamed")
        assert services.session.load(db, "chat", "new", "provider::model")["title"] == "Renamed"
        assert {row["session_id"] for row in services.session.list(db, "chat")} == {"default", "new"}
        assert services.session.list(db, "other-chat") == []
        assert services.session.delete(db, "chat", "new", "new") == (False, "active session")
    finally:
        db.close()


def test_session_create_obeys_outer_transaction_rollback(tmp_path):
    services, db = compose(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="abort"):
            with write_transaction(db):
                services.session.create(db, "chat", "provider::model", session_id="rollback", title="Rollback")
                raise RuntimeError("abort")
        assert services.session.list(db, "chat") == []
        assert db.execute("SELECT value FROM meta WHERE key='active_session:chat'").fetchone() is None
    finally:
        db.close()


def test_session_service_instances_keep_their_native_defaults_separate(tmp_path):
    first, db1 = compose(tmp_path / "one", "One.png")
    second, db2 = compose(tmp_path / "two", "Two.png")
    try:
        assert first.session.ensure(db1, "same", "provider::model")["character_file"] == "One.png"
        assert second.session.ensure(db2, "same", "provider::model")["character_file"] == "Two.png"
    finally:
        db1.close()
        db2.close()


def test_session_repository_rejects_standalone_writes(tmp_path):
    path = ROOT / "bridge/session_repository.py"
    assert path.is_file(), "missing session repository"
    repository = importlib.import_module("bridge.session_repository")
    services, db = compose(tmp_path)
    try:
        current = services.session.ensure(db, "chat", "provider::model")
        with pytest.raises(RuntimeError, match="transaction"):
            repository.update_session_row(db, "chat", current["session_id"], {"title": "invalid"}, 0.0)
        assert services.session.load(db, "chat", "default", "provider::model")["title"] == "Default session"
    finally:
        db.close()


ensure_application_extensions()


class SessionCommandRoutingTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = self.app_settings_builder.db_file
        self.app_settings_builder.db_file = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect(app_settings=self.app_settings_builder.build())
        self.session = _m_session_naming.create_session(
            self.db, "chat", "provider/model", session_id="active", app_settings=self.app_settings_builder.build()
        )
        _m_session_naming.update_session(self.db, "chat", "active", character_file="missing.png")

    def tearDown(self):
        self.db.close()
        self.app_settings_builder.db_file = self.old_db
        self.tmp.cleanup()

    def test_session_command_routes_before_character_card_load(self):
        calls = []
        old_menu = _m_message_commands.send_session_menu
        old_loader = _m_message_commands.card_fields_from_file
        _m_message_commands.send_session_menu = lambda _token, chat_id, sessions, active_id, *_args, **_kwargs: (
            calls.append((chat_id, sessions, active_id))
        )
        _m_message_commands.card_fields_from_file = lambda _name, *, app_settings=None: (_ for _ in ()).throw(
            AssertionError("character loader must not run")
        )
        try:
            make_test_conversation_service(app_settings=self.app_settings_builder.build()).process_message(
                self.db,
                "token",
                "key",
                "provider/model",
                {},
                "chat",
                "/session",
                telegram_message_id=1,
            )
        finally:
            _m_message_commands.send_session_menu = old_menu
            _m_message_commands.card_fields_from_file = old_loader
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "chat")
        self.assertEqual(calls[0][2], "active")
