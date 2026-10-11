"""Synthetic optimizer failures through the real HTTP and asynchronous saved-job route."""

import asyncio
import hashlib
import threading
from contextlib import closing

from aiohttp.test_utils import TestClient, TestServer
from miniapp_test_support import make_services, signed_data


def test_malformed_optimizer_error_survives_async_saved_job(tmp_path):
    from bridge.miniapp_config import load_miniapp_config
    from bridge.miniapp_http import create_miniapp_app
    from bridge.provider_port import ProviderPort

    services = make_services(tmp_path)
    released = threading.Event()
    calls = []
    suggestion = "Keep the synthetic setting and clarify motivation."

    def malformed(*args, **kwargs):
        calls.append(args)
        if not released.wait(timeout=5):
            raise AssertionError("Synthetic optimizer was not released")
        return "private synthetic malformed output"

    services.provider = ProviderPort(malformed)
    target = services.config.character_dir / "Alice.png"
    before = target.read_bytes()

    async def run():
        app = create_miniapp_app(services, load_miniapp_config(services.config))
        # Bound client startup, HTTP requests, job polling and cleanup together.
        async with asyncio.timeout(15):
            async with TestClient(TestServer(app)) as client:
                try:
                    headers = {"Authorization": "tma " + signed_data()}
                    response = await client.get("/api/v1/session", headers=headers)
                    assert response.status == 200
                    session = (await response.json())["session"]
                    response = await client.post(
                        "/api/v1/characters/Alice.png/optimize",
                        headers=headers,
                        json={
                            "operation_id": "synthetic-optimizer-error",
                            "session_id": session["session_id"],
                            "digest": hashlib.sha256(before).hexdigest(),
                            "suggestion": suggestion,
                        },
                    )
                    assert response.status == 200
                    accepted = await response.json()
                    assert accepted["state"] in {"queued", "running"}
                    released.set()
                    while True:
                        response = await client.get("/api/v1/jobs/" + accepted["id"], headers=headers)
                        assert response.status == 200
                        saved = await response.json()
                        if saved["state"] == "failed":
                            break
                        await asyncio.sleep(0.01)
                    assert saved["id"] == accepted["id"]
                    assert "could not produce a valid character preview" in saved["error"]
                    assert "resource is unavailable or changed" not in saved["error"]
                    assert "private synthetic" not in saved["error"]
                    assert len(calls) == 2  # Initial generation plus exactly one repair.
                    assert suggestion in "\n".join(str(message["content"]) for message in calls[0][2])
                    assert target.read_bytes() == before
                    with closing(services.db_factory()) as db:
                        assert db.execute("SELECT COUNT(*) FROM miniapp_jobs").fetchone()[0] == 1
                finally:
                    released.set()

    asyncio.run(run())
