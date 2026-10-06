"""Shared Codex transport/auth test helpers."""

from __future__ import annotations

import base64
import io
import json


def jwt_token(**claims):
    def segment(payload):
        raw = json.dumps(payload, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{segment({'alg': 'none'})}.{segment(claims)}.signature"


class StreamingResponse:
    status = 200

    def __init__(self, events):
        self.events = events
        self.buffer = io.BytesIO(b"".join(f"data: {json.dumps(event)}\n".encode() for event in events))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def readline(self, limit=-1):
        return self.buffer.readline(limit)

    def __iter__(self):
        for event in self.events:
            yield f"data: {json.dumps(event)}\n".encode()
