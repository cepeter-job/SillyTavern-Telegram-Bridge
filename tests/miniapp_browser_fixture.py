"""Isolated loopback API fixture for the Mini App DOM smoke test, never live services."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiohttp import web
from application_test_setup import make_native_test_persona_service
from miniapp_test_support import make_services, signed_data

import bridge.miniapp_system as system
from bridge.memory_diagnostics import MemoryDiagnostics
from bridge.miniapp_config import load_miniapp_config
from bridge.miniapp_http import create_miniapp_app
from bridge.model_router import ModelRouter
from bridge.provider_port import ProviderPort


async def serve() -> None:
    with tempfile.TemporaryDirectory(prefix="miniapp-dom-") as directory:
        services = make_services(Path(directory))
        services.memory_diagnostics = MemoryDiagnostics(services.config.bridge_home, {})
        from bridge.character_quality import store_character_rank

        db = services.db_factory()
        try:
            store_character_rank(db, "Alice.png", "S", app_settings=services.config)
        finally:
            db.close()
        if os.environ.get("MINIAPP_FIXTURE_USAGE") == "1":
            from miniapp_test_support import identity

            from bridge.miniapp_context import current_session
            from bridge.sqlite_store import write_transaction
            from bridge.token_usage_repository import insert_event

            who = identity()
            session = current_session(services, who, {})["session"]
            with services.db_factory() as usage_db:
                with write_transaction(usage_db):
                    for day in range(7):
                        for call in range(3):
                            input_count = (day + 1) * 500 + call * 130
                            output_count = 200 + day * 70
                            insert_event(
                                usage_db,
                                chat_id=who.chat_id,
                                session_id=session["session_id"],
                                model="fixture::story" if call != 2 else "fixture::utility",
                                purpose="story" if call != 2 else "choices",
                                created_at=time.time() - day * 86400,
                                status="succeeded",
                                elapsed_ms=1200,
                                input_tokens=input_count,
                                output_tokens=output_count,
                                total_tokens=input_count + output_count,
                                cached_tokens=input_count // 2,
                                reasoning_tokens=80,
                                reported=True,
                                complete=True,
                            )
        services.model_router = ModelRouter(load_catalog=lambda: {"test": {"models": ["model", "other"]}})
        services.provider = ProviderPort(
            generate_backend=lambda *args, **kwargs: json.dumps(
                {"description": "A thoughtful companion with clear motivations and consistent habits."}
            )
        )
        services.persona = make_native_test_persona_service(app_settings=services.config)
        services.telegram = SimpleNamespace(send_text=lambda *a: [], request=lambda *a: {})
        system.latest_bridge_release = lambda: ("0.2.099", "Fixture release; no deployment runs in this test.")
        system.installed_bridge_version = lambda **kwargs: "0.2.099"
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
        sock.setblocking(False)
        port = sock.getsockname()[1]
        # The real browser sends Origin on ES-module requests; the isolated
        # fixture must authorize its own loopback origin, not the example URL.
        config = replace(load_miniapp_config(services.config), public_url=f"http://127.0.0.1:{port}/miniapp/")
        runner = web.AppRunner(create_miniapp_app(services, config), access_log=None)
        await runner.setup()
        site = web.SockSite(runner, sock)
        try:
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            print(json.dumps({"url": f"http://127.0.0.1:{port}", "initData": signed_data()}), flush=True)
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(serve())
