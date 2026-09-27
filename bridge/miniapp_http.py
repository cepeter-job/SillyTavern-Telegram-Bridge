"""Bounded same-origin HTTP adapter; business rules belong to application owners."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from aiohttp import web

from bridge.miniapp_auth import MiniAppIdentity, authenticate
from bridge.miniapp_config import MiniAppConfig
from bridge.miniapp_errors import MiniAppError
from bridge.miniapp_types import ApiRoute, BinaryResult

_CSP = (
    "default-src 'none'; script-src 'self' https://telegram.org; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' blob: data:; connect-src 'self'; font-src 'self'; base-uri 'none'; "
    "form-action 'self'; object-src 'none'; frame-ancestors 'self' https://web.telegram.org https://*.telegram.org"
)
_ASSETS = {
    "index.html": "text/html",
    "app.js": "text/javascript",
    "ui.js": "text/javascript",
    "style.css": "text/css",
    "characters.js": "text/javascript",
    "models.js": "text/javascript",
    "management.js": "text/javascript",
}


def api_routes() -> list[ApiRoute]:
    from bridge.miniapp_characters import routes as character_routes
    from bridge.miniapp_context import current_session
    from bridge.miniapp_jobs import job_status, recent_jobs
    from bridge.miniapp_models import routes as model_routes
    from bridge.miniapp_sessions import routes as session_routes
    from bridge.miniapp_worlds import routes as world_routes

    return [
        ApiRoute("GET", "/session", current_session),
        ApiRoute("GET", "/jobs", recent_jobs),
        ApiRoute("GET", "/jobs/{job_id}", job_status),
        *character_routes(),
        *model_routes(),
        *session_routes(),
        *world_routes(),
    ]


def _error(status: int, code: str, message: str) -> web.Response:
    return web.json_response({"error": {"code": code, "message": message}}, status=status)


def create_miniapp_app(services: Any, config: MiniAppConfig) -> web.Application:
    """Construct the app without opening sockets, threads, database handles or providers."""
    users: dict[str, deque[float]] = defaultdict(deque)
    slots = asyncio.Semaphore(4)
    # Load only developer-declared assets once. No client input ever reaches a filesystem API.
    assets = {}
    for asset_name, content_type in _ASSETS.items():
        path = Path(__file__).with_name("miniapp_assets") / asset_name
        if path.is_file():
            assets[asset_name] = (path.read_bytes(), content_type)

    @web.middleware
    async def boundary(request: web.Request, handler: Any) -> web.StreamResponse:
        try:
            host = request.host.split(":", 1)[0].lower()
            if host not in {"127.0.0.1", "localhost", urlsplit(config.public_url).hostname}:
                response = _error(403, "origin", "Unrecognized request host.")
            elif request.headers.get("Origin", config.origin) != config.origin:
                response = _error(403, "origin", "Cross-origin requests are not accepted.")
            elif request.path.startswith("/api/") and request.headers.get("Sec-Fetch-Site") == "cross-site":
                response = _error(403, "origin", "Cross-origin requests are not accepted.")
            else:
                response = await handler(request)
        except web.HTTPException as exc:
            response = _error(exc.status, "http_error", "Request could not be served.")
        except MiniAppError as exc:
            response = _error(exc.status, exc.code, str(exc))
        except Exception as exc:
            logging.warning("Mini App request failed (%s)", type(exc).__name__)
            response = _error(500, "internal_error", "Operation failed; inspect the bridge logs.")
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "Content-Security-Policy": _CSP,
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            }
        )
        return response

    app = web.Application(middlewares=[boundary], client_max_size=config.body_limit)

    def identity(request: web.Request) -> MiniAppIdentity:
        header = request.headers.get("Authorization", "")
        try:
            if not header.startswith("tma "):
                raise ValueError("missing")
            who = authenticate(
                header[4:], services.config.bot_token, services.config.allowed_users, max_age=config.auth_max_age
            )
        except ValueError:
            raise MiniAppError(
                "Open the app again from an authorized Telegram account.", status=401, code="auth"
            ) from None
        now = time.monotonic()
        hits = users[who.user_id]
        while hits and hits[0] < now - 60:
            hits.popleft()
        if len(hits) >= 120:
            raise MiniAppError("Too many requests; try again shortly.", status=429, code="rate_limit")
        hits.append(now)
        return who

    async def me(request: web.Request) -> web.Response:
        who = identity(request)
        return web.json_response(
            {
                "user": {"id": who.user_id, "name": who.name},
                "scope": "private_chat",
                "features": ["dashboard"],
                "auth_expires_at": who.auth_date + config.auth_max_age,
            }
        )

    async def asset(request: web.Request) -> web.Response:
        name = request.match_info.get("asset", "index.html")
        resource = assets.get(name)
        if resource is None:
            raise web.HTTPNotFound()
        return web.Response(body=resource[0], content_type=resource[1])

    def adapt(route: ApiRoute) -> Any:
        async def handle(request: web.Request) -> web.Response:
            who = identity(request)
            values: dict[str, Any] = dict(request.query)
            if len(values) != len(request.query):
                raise MiniAppError("Duplicate request fields.")
            if request.can_read_body:
                if request.content_type != "application/json":
                    raise MiniAppError("Use a JSON request body.", status=415)
                try:
                    data = json.loads(await request.text(), parse_constant=lambda _: None)
                except (ValueError, UnicodeError):
                    raise MiniAppError("Invalid JSON body.") from None
                if not isinstance(data, dict) or set(values) & set(data):
                    raise MiniAppError("Invalid or duplicate request fields.")
                values.update(data)
            if set(request.match_info) & set(values):
                raise MiniAppError("Path and body fields must not overlap.")
            values.update(request.match_info)
            try:
                await asyncio.wait_for(slots.acquire(), timeout=0.1)
            except TimeoutError:
                raise MiniAppError("Management service is busy; retry shortly.", status=503, code="busy") from None
            try:
                if route.background_kind:
                    from bridge.miniapp_jobs import submit_job

                    result = await asyncio.to_thread(
                        submit_job, services, who, route.background_kind, values, route.handler
                    )
                else:
                    result = await asyncio.to_thread(route.handler, services, who, values)
            finally:
                slots.release()
            if isinstance(result, BinaryResult):
                return web.Response(body=result.content, content_type=result.content_type)
            return web.json_response(result)

        return handle

    app.router.add_get("/miniapp/", asset)
    app.router.add_get("/miniapp/{asset}", asset)
    app.router.add_get("/api/v1/me", me)
    for route in api_routes():
        app.router.add_route(route.method, "/api/v1" + route.path, adapt(route))
    return app
