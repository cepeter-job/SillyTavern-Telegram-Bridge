"""Test-only gates and counters around the real summary handler and executor."""

from __future__ import annotations

import json
import threading
from contextlib import closing
from typing import Any

from aiohttp import web

from bridge.provider_port import ProviderPort
from bridge.sqlite_store import write_transaction


class SummaryRecoveryFixture:
    def __init__(self, services: Any, who: Any, session: dict) -> None:
        import bridge.miniapp_memory as memory

        self.services = services
        self.released = threading.Event()
        self.lock = threading.Lock()
        self.handlers = 0
        self.providers = 0
        original = memory.regenerate_summary

        def observed_summary(*args: Any, **kwargs: Any) -> dict:
            with self.lock:
                self.handlers += 1
            return original(*args, **kwargs)

        # Instrument the actual route handler; keep all session, summary,
        # persistence and provider-adapter behavior below it intact.
        memory.regenerate_summary = observed_summary
        services.provider = ProviderPort(generate_backend=self.generate)
        with closing(services.db_factory()) as db, write_transaction(db):
            db.execute(
                "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,'user',?,1)",
                (who.chat_id, session["session_id"], "Synthetic story: the lighthouse keeper carries a brass key."),
            )

    def generate(self, *args: Any, **kwargs: Any) -> str:
        with self.lock:
            self.providers += 1
        if not self.released.wait(timeout=60):
            raise RuntimeError("Browser test did not release its synthetic provider.")
        return json.dumps(
            {"blocks": [{"text": "Synthetic lighthouse continuity", "visibility": "shared", "known_by": []}]}
        )

    def install(self, app: web.Application) -> None:
        async def metrics(_request: web.Request) -> web.Response:
            with closing(self.services.db_factory()) as db:
                exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='miniapp_jobs'").fetchone()
                rows = (
                    db.execute("SELECT id,request_key,state FROM miniapp_jobs ORDER BY created_at").fetchall()
                    if exists
                    else []
                )
            with self.lock:
                result = {"handlers": self.handlers, "providers": self.providers}
            result["jobs"] = [{"id": row[0], "operation_id": row[1], "state": row[2]} for row in rows]
            return web.json_response(result)

        async def release(_request: web.Request) -> web.Response:
            self.released.set()
            return web.json_response({"released": True})

        # This app and these routes exist only in the isolated child fixture.
        app.router.add_get("/fixture/metrics", metrics)
        app.router.add_post("/fixture/release", release)
