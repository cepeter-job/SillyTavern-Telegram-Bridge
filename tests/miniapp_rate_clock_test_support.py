"""Opt-in loopback fixture clock; production authentication and rate rules stay real."""

from __future__ import annotations

import secrets
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp import web

import bridge.miniapp_http as http


@contextmanager
def rate_clock_control(app):
    # Replace only the HTTP adapter's module reference, not stdlib time functions.
    # Real browser fixtures do not opt in; other application timers stay real.
    now = http.time.monotonic()
    key = secrets.token_urlsafe(24)
    path = "/fixture/rate-window"

    async def advance(request):
        nonlocal now
        supplied = request.headers.get("X-Fixture-Rate-Clock", "")
        if not secrets.compare_digest(supplied.encode(), key.encode()):
            raise web.HTTPForbidden()
        now += 61
        return web.json_response({"advanced_seconds": 61})

    app.router.add_post(path, advance)
    with patch.object(http, "time", SimpleNamespace(monotonic=lambda: now)):
        yield {"path": path, "key": key}
