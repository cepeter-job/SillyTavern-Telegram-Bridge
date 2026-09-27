import asyncio
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer
from miniapp_test_support import TOKEN, signed_data
from settings_test_support import make_test_settings


def settings(tmp_path, **extra):
    return make_test_settings(
        home=tmp_path,
        bot_token=TOKEN,
        allowed_users=frozenset({"12345"}),
        bridge_home=tmp_path,
        environ={"SILLYTAVERN_MINIAPP_PUBLIC_URL": "https://bridge.example/miniapp/", **extra},
    )


def test_disabled_and_public_url_validation(tmp_path):
    from bridge.miniapp_config import load_miniapp_config

    s = make_test_settings(home=tmp_path, environ={})
    assert not load_miniapp_config(s).enabled
    assert load_miniapp_config(settings(tmp_path)).host == "127.0.0.1"
    for url in [
        "http://bridge.example/miniapp/",
        "https://x:pwd@bridge.example/",
        "https://bridge.example/wrong",
        "https://bridge.example/miniapp/?token=bad",
    ]:
        with pytest.raises(ValueError):
            load_miniapp_config(settings(tmp_path, SILLYTAVERN_MINIAPP_PUBLIC_URL=url))


def test_http_auth_assets_and_origin_contract(tmp_path):
    from bridge.miniapp_config import load_miniapp_config
    from bridge.miniapp_http import create_miniapp_app

    s = settings(tmp_path)

    async def run():
        app = create_miniapp_app(SimpleNamespace(config=s), load_miniapp_config(s))
        async with TestClient(TestServer(app)) as client:
            response = await client.get("/miniapp/")
            assert response.status == 200
            assert "telegram-web-app.js" in await response.text()
            assert "script-src" in response.headers["Content-Security-Policy"]
            assert (await client.get("/api/v1/me")).status == 401
            headers = {"Authorization": "tma " + signed_data()}
            response = await client.get("/api/v1/me", headers=headers)
            assert response.status == 200
            data = await response.json()
            assert data["user"]["id"] == "12345"
            assert TOKEN not in str(data)
            assert response.headers["Cache-Control"] == "no-store"
            assert (await client.get("/api/v1/me", headers={**headers, "Origin": "https://evil.example"})).status == 403
            assert (await client.get("/api/v1/me", headers={**headers, "Host": "evil.example"})).status == 403
            assert (await client.get("/miniapp/.env")).status == 404
            assert (
                await client.get("/api/v1/me", headers={"Authorization": "tma " + signed_data(user_id=999)})
            ).status == 401

    asyncio.run(run())


def test_runtime_starts_and_stops_loopback_listener(tmp_path):
    import socket

    from bridge.miniapp_config import load_miniapp_config
    from bridge.miniapp_runtime import MiniAppRuntime

    s = settings(tmp_path)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    s = settings(tmp_path, SILLYTAVERN_MINIAPP_PORT=str(port))
    from bridge.sqlite_store import db_connect

    server = MiniAppRuntime(
        SimpleNamespace(config=s, db_factory=lambda: db_connect(app_settings=s)), load_miniapp_config(s)
    )
    with server:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            client.sendall(b"GET /miniapp/ HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
            assert b"200 OK" in client.recv(1024)
    assert not server.thread.is_alive()


def test_static_requests_never_open_a_client_derived_path(tmp_path, monkeypatch):
    from pathlib import Path

    from bridge.miniapp_config import load_miniapp_config
    from bridge.miniapp_http import create_miniapp_app

    s = settings(tmp_path)

    async def run():
        app = create_miniapp_app(SimpleNamespace(config=s), load_miniapp_config(s))
        async with TestClient(TestServer(app)) as client:

            def forbidden_read(_path):
                raise AssertionError("request-time filesystem reads are forbidden")

            with monkeypatch.context() as patch:
                patch.setattr(Path, "read_bytes", forbidden_read)
                response = await client.get("/miniapp/app.js")
                assert response.status == 200
                assert "function api" in await response.text()

    asyncio.run(run())


def test_character_routes_require_auth_and_keep_private_scope(tmp_path):
    from miniapp_test_support import make_services

    from bridge.miniapp_config import load_miniapp_config
    from bridge.miniapp_http import create_miniapp_app

    services = make_services(tmp_path)

    async def run():
        app = create_miniapp_app(services, load_miniapp_config(services.config))
        async with TestClient(TestServer(app)) as client:
            headers = {"Authorization": "tma " + signed_data()}
            assert (await client.get("/api/v1/characters")).status == 401
            result = await client.get("/api/v1/characters", headers=headers)
            assert result.status == 200
            data = await result.json()
            assert data["session"]["chat_id"] == "12345"
            portrait = await client.get("/api/v1/characters/Alice.png/portrait", headers=headers)
            assert portrait.status == 200 and portrait.content_type == "image/png"
            assert (await client.get("/miniapp/characters.js")).status == 200
            denied = await client.post(
                "/api/v1/characters/Alice.png/select", headers=headers, json={"session_id": "foreign", "confirm": True}
            )
            assert denied.status == 409
            selected = await client.post(
                "/api/v1/characters/Alice.png/select",
                headers=headers,
                json={"session_id": "default", "confirm": True, "chat_id": "67890"},
            )
            assert selected.status == 200
            assert (await selected.json())["session"]["chat_id"] == "12345"

    asyncio.run(run())


def test_menu_registration_failure_does_not_prevent_other_users(tmp_path):
    from dataclasses import replace

    from bridge.miniapp_config import load_miniapp_config
    from bridge.miniapp_runtime import configure_miniapp_menu

    s = replace(settings(tmp_path), allowed_users=frozenset({"12345", "67890"}))
    seen = []

    def request(token, method, payload):
        seen.append(payload.get("chat_id", "default"))
        if payload.get("chat_id") == "12345":
            raise RuntimeError("Bot has not been started by this user")

    configure_miniapp_menu(SimpleNamespace(config=s, telegram=SimpleNamespace(request=request)), load_miniapp_config(s))
    assert seen == ["default", "12345", "67890"]


def test_every_management_page_has_auth_and_no_secret_response(tmp_path, monkeypatch):
    from miniapp_test_support import make_services

    import bridge.miniapp_system as system
    from bridge.miniapp_config import load_miniapp_config
    from bridge.miniapp_http import create_miniapp_app

    services = make_services(tmp_path)
    monkeypatch.setattr(system, "latest_bridge_release", lambda: ("0.2.099", "Test release"))

    async def run():
        app = create_miniapp_app(services, load_miniapp_config(services.config))
        async with TestClient(TestServer(app)) as client:
            for page in [
                "characters",
                "models",
                "generation",
                "sessions",
                "personas",
                "worlds",
                "memory",
                "databank",
                "status",
                "update",
                "jobs",
            ]:
                assert (await client.get("/api/v1/" + page)).status == 401
                response = await client.get("/api/v1/" + page, headers={"Authorization": "tma " + signed_data()})
                assert response.status == 200, page
                assert TOKEN not in await response.text()
            for asset in ["app.js", "characters.js", "models.js", "management.js", "memory.js", "system.js"]:
                assert (await client.get("/miniapp/" + asset)).status == 200, asset

    asyncio.run(run())


def test_default_menu_is_available_to_new_private_chats(tmp_path):
    from bridge.miniapp_config import load_miniapp_config
    from bridge.miniapp_runtime import configure_miniapp_menu

    s = settings(tmp_path)
    requests = []
    configure_miniapp_menu(
        SimpleNamespace(config=s, telegram=SimpleNamespace(request=lambda *args: requests.append(args))),
        load_miniapp_config(s),
    )
    assert any("chat_id" not in args[2] and args[2]["menu_button"]["type"] == "web_app" for args in requests)


def test_rank_webm_assets_use_canonical_files_and_nested_route(tmp_path, monkeypatch):
    from pathlib import Path

    from bridge.miniapp_config import load_miniapp_config
    from bridge.miniapp_http import create_miniapp_app

    s = settings(tmp_path)
    canonical = Path(__file__).parents[1] / "assets/character-ranks/telegram/rank_S.webm"
    expected = canonical.read_bytes()

    async def run():
        app = create_miniapp_app(SimpleNamespace(config=s), load_miniapp_config(s))
        async with TestClient(TestServer(app)) as client:
            with monkeypatch.context() as patch:
                patch.setattr(
                    Path, "read_bytes", lambda _path: (_ for _ in ()).throw(AssertionError("request-time read"))
                )
                response = await client.get("/miniapp/ranks/rank_S.webm")
                assert response.status == 200
                assert response.content_type == "video/webm"
                assert await response.read() == expected
                assert "media-src 'self'" in response.headers["Content-Security-Policy"]
                assert (await client.get("/miniapp/ranks/rank_Z.webm")).status == 404

    asyncio.run(run())
