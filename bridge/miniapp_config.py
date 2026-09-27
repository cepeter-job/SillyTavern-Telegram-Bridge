"""Opt-in Mini App configuration derived from the immutable settings snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from bridge.config_values import read_int
from bridge.settings import AppSettings


@dataclass(frozen=True)
class MiniAppConfig:
    public_url: str = ""
    host: str = "127.0.0.1"
    port: int = 8787
    auth_max_age: int = 3600
    body_limit: int = 20 * 1024 * 1024

    @property
    def enabled(self) -> bool:
        return bool(self.public_url)

    @property
    def origin(self) -> str:
        parts = urlsplit(self.public_url)
        return f"{parts.scheme}://{parts.netloc}"


def load_miniapp_config(settings: AppSettings) -> MiniAppConfig:
    values = settings.environ
    url = values.get("SILLYTAVERN_MINIAPP_PUBLIC_URL", "").strip()
    if url:
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
            or parts.path not in {"", "/", "/miniapp", "/miniapp/"}
            or any(ch.isspace() or ord(ch) < 32 for ch in url)
        ):
            raise ValueError("Mini App public URL must be an HTTPS origin or its /miniapp/ path")
        if parts.port is not None and not 1 <= parts.port <= 65535:
            raise ValueError("Mini App public port is invalid")
        url = f"https://{parts.netloc.lower()}/miniapp/"
    return MiniAppConfig(
        public_url=url,
        port=read_int(values, "SILLYTAVERN_MINIAPP_PORT", 8787, minimum=1024, maximum=65535),
        auth_max_age=read_int(values, "SILLYTAVERN_MINIAPP_AUTH_MAX_AGE", 3600, minimum=60, maximum=86400),
    )
