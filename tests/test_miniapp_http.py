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
    server = MiniAppRuntime(SimpleNamespace(config=s), load_miniapp_config(s))
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
