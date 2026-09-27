"""Isolated loopback API fixture for the Mini App DOM smoke test, never live services."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiohttp import web
from application_test_setup import make_native_test_persona_service
from miniapp_test_support import make_services, signed_data

import bridge.miniapp_system as system
from bridge.miniapp_config import load_miniapp_config
from bridge.miniapp_http import create_miniapp_app
from bridge.model_router import ModelRouter
from bridge.provider_port import ProviderPort


async def serve() -> None:
    with tempfile.TemporaryDirectory(prefix="miniapp-dom-") as directory:
        services = make_services(Path(directory))
        from bridge.character_quality import store_character_rank

        db = services.db_factory()
        try:
            store_character_rank(db, "Alice.png", "S", app_settings=services.config)
        finally:
            db.close()
        services.model_router = ModelRouter(load_catalog=lambda: {"test": {"models": ["model", "other"]}})
        services.provider = ProviderPort(
            generate_backend=lambda *args, **kwargs: json.dumps(
                {"description": "A thoughtful companion with clear motivations and consistent habits."}
            )
        )
        services.persona = make_native_test_persona_service(app_settings=services.config)
        services.telegram = SimpleNamespace(send_text=lambda *a: [], request=lambda *a: {})
        system.latest_bridge_release = lambda: ("0.2.099", "Fixture release; no deployment runs in this test.")
        runner = web.AppRunner(create_miniapp_app(services, load_miniapp_config(services.config)), access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        try:
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            print(json.dumps({"url": f"http://127.0.0.1:{port}", "initData": signed_data()}), flush=True)
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(serve())
