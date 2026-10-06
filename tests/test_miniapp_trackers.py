"""Authenticated tracker reads stay in the caller's active private session."""

import asyncio
import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer
from miniapp_test_support import TOKEN, card_bytes, identity, signed_data
from settings_test_support import make_test_settings
from test_simulation_trackers import _db
from tracker_view_test_support import seed_trackers

from bridge.meta_repository import store_meta_value
from bridge.session_repository import insert_session_row, load_session_row
from bridge.sqlite_store import write_transaction


@pytest.fixture
def services(tmp_path):
    config = make_test_settings(
        home=tmp_path,
        bot_token=TOKEN,
        allowed_users=frozenset({"12345", "67890"}),
        default_character_file="Default.png",
        environ={"SILLYTAVERN_MINIAPP_PUBLIC_URL": "https://bridge.example/miniapp/"},
    )
    config.character_dir.mkdir(parents=True, exist_ok=True)
    config.card_file.write_bytes(card_bytes("Alice"))
    path = tmp_path / "trackers.sqlite3"
    with closing(_db()) as original, closing(sqlite3.connect(path)) as target:
        with write_transaction(original):
            original.execute(
                "UPDATE sessions SET chat_id='12345',session_id='default',character_file=?",
                (config.default_character_file,),
            )
            session = load_session_row(original, "12345", "default")
            insert_session_row(original, session | {"session_id": "other", "title": "Other story"}, 2.0)
            insert_session_row(original, session | {"chat_id": "67890", "title": "Foreign story"}, 2.0)
        seed_trackers(original, "12345", "default")
        original.backup(target)

    def read_connection():
        connection = sqlite3.connect(path)
        connection.execute("PRAGMA query_only=ON")
        return connection

    return SimpleNamespace(config=config, db_factory=read_connection, db_path=path)


def get(services, user="12345", **values):
    from bridge.miniapp_trackers import get_trackers

    return get_trackers(services, identity(user), values)


def test_query_fields_cannot_retarget_the_authenticated_active_session(services):
    result = get(services, chat_id="67890", session_id="other", through_rowid="999999", narrator=True)
    assert result["session"] == {"session_id": "default", "title": "s1"}
    assert result["inventory"][0]["name"] == "Brass key"
    assert result["agendas"][0]["name"] == "Rowan"
    assert "Secret" not in str(result)
    with closing(sqlite3.connect(services.db_path)) as db, write_transaction(db):
        store_meta_value(db, "active_session:12345", "other")
    changed = get(services, session_id="default")
    assert changed["session"] == {"session_id": "other", "title": "Other story"}
    assert changed["inventory"] == changed["checks"] == []
    assert get(services, "67890")["inventory"] == []


def test_fresh_chat_has_an_empty_view_without_creating_a_session(services):
    result = get(services, "99999")
    assert result["session"] is None
    assert result["inventory"] == result["relationships"] == result["checks"] == []
    assert result["pending"] is False
    with closing(sqlite3.connect(services.db_path)) as db:
        assert db.execute("SELECT COUNT(*) FROM sessions WHERE chat_id='99999'").fetchone()[0] == 0


def test_missing_character_still_shows_public_saved_state_and_hides_private_agendas(services):
    services.config.card_file.unlink()
    result = get(services)
    assert result["inventory"][0]["name"] == "Brass key"
    assert result["agendas"][0]["name"] == "Rowan"
    assert "Secret" not in str(result)


def test_tracker_http_route_requires_auth_and_serves_its_registered_asset(services):
    from bridge.miniapp_config import load_miniapp_config
    from bridge.miniapp_http import create_miniapp_app

    async def run():
        app = create_miniapp_app(services, load_miniapp_config(services.config))
        async with TestClient(TestServer(app)) as client:
            assert (await client.get("/api/v1/trackers")).status == 401
            headers = {"Authorization": "tma " + signed_data()}
            response = await client.get(
                "/api/v1/trackers?chat_id=67890&session_id=other&narrator=true",
                headers=headers,
            )
            assert response.status == 200
            assert response.headers["Cache-Control"] == "no-store"
            result = await response.json()
            assert result["inventory"][0]["name"] == "Brass key"
            assert "Secret" not in str(result) and TOKEN not in str(result)
            assert (await client.post("/api/v1/trackers", headers=headers, json={})).status == 405
            assert (await client.get("/miniapp/trackers.js")).status == 200
            assert "trackers" in (await (await client.get("/api/v1/me", headers=headers)).json())["features"]
            foreign = await client.get(
                "/api/v1/trackers", headers={"Authorization": "tma " + signed_data(user_id=67890)}
            )
            assert (await foreign.json())["inventory"] == []

    asyncio.run(run())
