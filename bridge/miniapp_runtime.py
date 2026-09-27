"""Explicit lifecycle of the optional loopback web server within the bot process."""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from types import TracebackType
from typing import Any

from aiohttp import web

from bridge.miniapp_config import MiniAppConfig
from bridge.miniapp_http import create_miniapp_app


class MiniAppRuntime:
    def __init__(self, services: Any, config: MiniAppConfig) -> None:
        self.services = services
        self.config = config
        self.ready = threading.Event()
        self.error: BaseException | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.stop_event: asyncio.Event | None = None
        self.thread = threading.Thread(target=self._run, name="st-miniapp", daemon=True)

    async def _serve(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.loop.set_default_executor(ThreadPoolExecutor(max_workers=4, thread_name_prefix="st-miniapp-api"))
        self.stop_event = asyncio.Event()
        runner = web.AppRunner(create_miniapp_app(self.services, self.config), access_log=None, shutdown_timeout=5)
        try:
            await runner.setup()
            await web.TCPSite(runner, self.config.host, self.config.port).start()
            from bridge.miniapp_jobs import recover_interrupted_jobs

            recover_interrupted_jobs(self.services)
            self.ready.set()
            await self.stop_event.wait()
        finally:
            await runner.cleanup()

    def _run(self) -> None:
        try:
            asyncio.run(self._serve())
        except BaseException as exc:
            self.error = exc
        finally:
            self.ready.set()

    def __enter__(self) -> MiniAppRuntime:
        health = getattr(self.services, "health", None)
        if health is not None:
            health.begin(self.services.config)
        if self.config.enabled:
            self.thread.start()
            if not self.ready.wait(10):
                self.close()
                raise RuntimeError("Mini App listener startup timed out")
            if self.error is not None:
                self.thread.join(2)
                raise RuntimeError("Mini App listener could not start; check the configured port") from self.error
        return self

    def close(self) -> None:
        if self.thread.is_alive() and self.loop is not None and self.stop_event is not None:
            self.loop.call_soon_threadsafe(self.stop_event.set)
            self.thread.join(10)
            if self.thread.is_alive():
                raise RuntimeError("Mini App listener did not stop within its shutdown deadline")

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.close()


def configure_miniapp_menu(services: Any, config: MiniAppConfig) -> None:
    if config.enabled:
        menu = {"type": "web_app", "text": "Bridge", "web_app": {"url": config.public_url}}
        # Set the default for users who have not opened their first private bot chat yet.
        for user_id in [None, *sorted(services.config.allowed_users)]:
            payload = {"menu_button": menu}
            if user_id is not None:
                payload["chat_id"] = user_id
            try:
                services.telegram.request(services.config.bot_token, "setChatMenuButton", payload)
            except Exception as exc:
                logging.warning("Mini App menu registration deferred (%s)", type(exc).__name__)
