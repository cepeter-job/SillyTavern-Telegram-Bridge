"""Shared provider-attempt fixtures for budget tests."""

from __future__ import annotations

import io
import json

from settings_test_support import make_test_settings

from bridge.model_router import ModelRouter


class Response:
    status = 200

    def __init__(self, content, reason="stop", streaming=False):
        event = {"choices": [{"message": {"content": content}, "delta": {"content": content}, "finish_reason": reason}]}
        self.raw = (f"data: {json.dumps(event)}\n\ndata: [DONE]\n" if streaming else json.dumps(event)).encode()
        self.buffer = io.BytesIO(self.raw)

    def read(self, size=-1):
        return self.buffer.read(size)

    def readline(self, size=-1):
        return self.buffer.readline(size)

    def __iter__(self):
        return iter(self.raw.splitlines(keepends=True))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def setup_route(tmp_path, *, window=8000, transport="openai_compatible", streaming=False, extra=None):
    spec = {
        "models": ["synthetic"],
        "transport": transport,
        "context_window_tokens": window,
        "api_endpoint": "https://example.com/v1",
        "api_key_env": "SYNTHETIC_KEY",
        "streaming": streaming,
    }
    catalog = {"p": spec, **(extra or {})}
    path = tmp_path / "providers.json"
    path.write_text(json.dumps({"providers": catalog}))
    settings = make_test_settings(
        {"SILLYTAVERN_PROVIDER_ALLOWED_HOSTS": "example.com", "SYNTHETIC_KEY": "synthetic-only"},
        home=tmp_path,
        provider_config_file=path,
        context_window_tokens=window,
    )
    return settings, ModelRouter(load_catalog=lambda: catalog)
