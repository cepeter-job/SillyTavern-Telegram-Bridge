"""Diagnostics reuse Mini App ownership checks and never accept server paths."""

import asyncio
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer
from miniapp_test_support import TOKEN, identity, make_services, signed_data

from bridge.diagnostic_events import scope_reference


def setup(tmp_path):
    from bridge.miniapp_context import current_session

    services = make_services(tmp_path)
    who = identity()
    session = current_session(services, who, {})["session"]
    path = services.config.log_file
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "schema": 1,
        "timestamp": "2026-10-08T06:00:00.000Z",
        "event": "provider.finish",
        "level": "INFO",
        "chat_ref": scope_reference("chat", who.chat_id),
        "session_ref": scope_reference("session", session["session_id"]),
        "request_id": "tg-42",
        "purpose": "story",
        "status": "failed",
        "reason": "timeout",
        "prompt": "PRIVATE_STORY",
    }
    other = {**event, "chat_ref": scope_reference("chat", "67890"), "model": "OTHER_PRIVATE_MODEL"}
    path.write_text(json.dumps(event) + "\n" + json.dumps(other) + "\n", encoding="utf-8")
    return services, who, session


def test_timeline_and_export_are_scoped_and_content_free(tmp_path):
    from bridge.miniapp_diagnostics import diagnostic_export, diagnostic_timeline

    services, who, session = setup(tmp_path)
    values = {"session_id": session["session_id"]}
    timeline = diagnostic_timeline(services, who, values)
    export = diagnostic_export(services, who, values)
    for result in (timeline, export):
        assert len(result["events"]) == 1
        assert result["events"][0]["request_id"] == "tg-42"
        assert result["usage"]["complete"] is False
        encoded = json.dumps(result)
        for forbidden in ("PRIVATE_STORY", "OTHER_PRIVATE_MODEL", TOKEN, str(tmp_path), '"chat_id"', '"session_id"'):
            assert forbidden not in encoded
    assert export["schema"] == 1
    assert export["kind"] == "sillytavern-diagnostics"
    assert "generated_at" in export


@pytest.mark.parametrize(
    "values",
    [
        {"path": "/etc/passwd"},
        {"log_file": "other.log"},
        {"chat_id": "67890"},
        {"limit": "0"},
        {"limit": "501"},
        {"limit": True},
        {"level": "SECRET"},
        {"request_id": "raw private content"},
        {"purpose": ["story"]},
    ],
)
def test_client_cannot_choose_files_owners_or_unbounded_filters(tmp_path, values):
    from bridge.miniapp_diagnostics import diagnostic_timeline
    from bridge.miniapp_errors import MiniAppError

    services, who, _session = setup(tmp_path)
    with pytest.raises(MiniAppError):
        diagnostic_timeline(services, who, values)


def test_foreign_and_stale_session_cannot_export_another_timeline(tmp_path):
    from bridge.miniapp_diagnostics import diagnostic_export, diagnostic_timeline
    from bridge.miniapp_errors import MiniAppError

    services, who, session = setup(tmp_path)
    assert diagnostic_timeline(services, identity("67890"), {})["events"] == []
    with pytest.raises(MiniAppError) as failure:
        diagnostic_export(services, who, {"session_id": "not-the-active-session"})
    assert failure.value.status == 409
    assert "events" not in str(failure.value)
    assert diagnostic_export(services, who, {"session_id": session["session_id"]})["events"]


def test_http_diagnostics_auth_origin_assets_and_get_only(tmp_path):
    from bridge.miniapp_config import load_miniapp_config
    from bridge.miniapp_http import create_miniapp_app

    services, _who, _session = setup(tmp_path)

    async def run():
        app = create_miniapp_app(services, load_miniapp_config(services.config))
        async with TestClient(TestServer(app)) as client:
            headers = {"Authorization": "tma " + signed_data()}
            for path in ("/api/v1/diagnostics", "/api/v1/diagnostics/export"):
                assert (await client.get(path)).status == 401
                response = await client.get(path, headers=headers)
                assert response.status == 200
                assert response.headers["Cache-Control"] == "no-store"
                assert len((await response.json())["events"]) == 1
                assert (await client.get(path, headers={**headers, "Origin": "https://evil.example"})).status == 403
                assert (await client.post(path, headers=headers, json={})).status == 405
            asset = await client.get("/miniapp/diagnostics.js")
            assert asset.status == 200
            assert "innerHTML" not in await asset.text()

    asyncio.run(run())
