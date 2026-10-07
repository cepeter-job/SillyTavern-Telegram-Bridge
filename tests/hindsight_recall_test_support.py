"""Coordinated, socket-free foreground recall test dependencies."""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace


class Clock:
    def __init__(self):
        self.offset = 0.0

    def __call__(self):
        return time.monotonic() + self.offset

    def advance(self, seconds):
        self.offset += seconds


def response(*identities):
    return SimpleNamespace(
        results=[SimpleNamespace(document_id=doc, type=kind, text="UNTRUSTED REMOTE TEXT") for doc, kind in identities]
    )


class Gate:
    def __init__(self, *, resistant=False, result=None):
        self.entered = threading.Event()
        self.cancelled = threading.Event()
        self.finished = threading.Event()
        self.resistant = resistant
        self.result = result or response(("native-document", "world"))
        self.loop = None
        self.event = None

    async def wait(self):
        self.loop = asyncio.get_running_loop()
        self.event = asyncio.Event()
        self.entered.set()
        try:
            while True:
                try:
                    await self.event.wait()
                    return self.result
                except asyncio.CancelledError:
                    self.cancelled.set()
                    if not self.resistant:
                        raise
        finally:
            self.finished.set()

    def release(self):
        if self.loop is not None and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.event.set)


class Client:
    def __init__(self, behavior=None):
        self.behavior = behavior
        self.calls = []
        self.loops = []
        self.closed = False

    async def arecall(self, **kwargs):
        self.calls.append(kwargs)
        self.loops.append(asyncio.get_running_loop())
        if self.behavior is not None:
            return await self.behavior(kwargs)
        return response(("native-document", "world"))

    async def aclose(self):
        self.loops.append(asyncio.get_running_loop())
        self.closed = True


def runtime_for(client, **kwargs):
    from bridge.hindsight_recall_runtime import HindsightRecallRuntime

    return HindsightRecallRuntime(
        base_url="http://127.0.0.1:8888", api_key=None, client_factory=lambda **_kwargs: client, **kwargs
    )


def recall(runtime, query="silver key"):
    return runtime.recall(bank_id="bank", session_id="story", query=query, max_tokens=1600)


def identity_port(client):
    """Adapt existing synthetic synchronous responses at the new identity boundary."""

    def remote(**kwargs):
        return tuple((item.document_id, item.type) for item in client.recall(**kwargs).results)

    return remote


class StatusError(Exception):
    def __init__(self, status):
        self.status = status
