"""One loop-owned optional recall client with bounded synchronous caller admission."""

from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, TimeoutError
from dataclasses import dataclass, field
from itertools import islice
from typing import Any, Literal

from bridge.hindsight_endpoint import validated_hindsight_base_url

RECALL_DEADLINE_SECONDS = 1.5
RECALL_COOLDOWN_SECONDS = 30.0
RECALL_MAX_IN_FLIGHT = 2
RECALL_SHUTDOWN_SECONDS = 3.0
RECALL_MAX_RESULTS = 64
RecallIdentities = tuple[tuple[str, str], ...]
Health = Literal["success", "availability", "configuration", "cancelled"]


def _client_factory(**kwargs: Any) -> Any:
    from hindsight_client import Hindsight

    return Hindsight(**kwargs)


def _failure_kind(exc: Exception) -> Health:
    status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
    if status == 404:
        return "success"
    if status == 429 or (isinstance(status, int) and 500 <= status < 600):
        return "availability"
    if isinstance(exc, (OSError, TimeoutError)) or type(exc).__module__.startswith("aiohttp."):
        return "availability"
    return "configuration"


def _identities(response: Any) -> RecallIdentities:
    results = getattr(response, "results", None)
    if not isinstance(results, (list, tuple)):
        raise ValueError("Invalid recall response")
    accepted = []
    seen: set[str] = set()
    for item in islice(results, RECALL_MAX_RESULTS):
        document_id, kind = getattr(item, "document_id", None), getattr(item, "type", None)
        if (
            isinstance(document_id, str)
            and 0 < len(document_id) <= 512
            and kind in {"world", "experience"}
            and document_id not in seen
        ):
            seen.add(document_id)
            accepted.append((document_id, kind))
    return tuple(accepted)


@dataclass(eq=False)
class _Request:
    bank_id: str
    session_id: str
    query: str
    max_tokens: int
    deadline: float
    generation: int
    probe: bool
    completion: Future[RecallIdentities] = field(default_factory=Future)
    task: asyncio.Task[tuple[RecallIdentities, Health]] | None = None
    abandoned: bool = False
    health_reported: bool = False


class HindsightRecallRuntime:
    """Construction owns no threads or transport; start/close belong to the application."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        timeout_seconds: float = RECALL_DEADLINE_SECONDS,
        cooldown_seconds: float = RECALL_COOLDOWN_SECONDS,
        max_in_flight: int = RECALL_MAX_IN_FLIGHT,
        clock: Callable[[], float] = time.monotonic,
        client_factory: Callable[..., Any] = _client_factory,
    ) -> None:
        self._base_url = validated_hindsight_base_url(base_url)[0]
        if not all(math.isfinite(value) and value > 0 for value in (timeout_seconds, cooldown_seconds)):
            raise ValueError("Recall deadlines must be finite and positive")
        if not 1 <= max_in_flight <= RECALL_MAX_IN_FLIGHT:
            raise ValueError("Recall admission must be between one and two")
        self._api_key, self._factory, self._clock = api_key, client_factory, clock
        self._timeout, self._cooldown, self._capacity = timeout_seconds, cooldown_seconds, max_in_flight
        self._lock = threading.Lock()
        self._started = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._client: Any = None
        self._client_closed = False
        self._accepting = False
        self._stopping = False
        self._records: set[_Request] = set()
        self._generation = 0
        self._open_until = 0.0
        self._probe_in_flight = False

    def start(self) -> None:
        with self._lock:
            if self._thread is not None or self._stopping:
                return
            self._thread = threading.Thread(target=self._run_owner, name="st-hindsight-recall", daemon=False)
            self._thread.start()
        if not self._started.wait(self._timeout):
            logging.warning("Optional Hindsight recall startup deadline exceeded")
            self.begin_shutdown()

    def _run_owner(self) -> None:
        try:
            with asyncio.Runner() as runner:
                runner.run(self._own_client())
        except Exception:
            logging.warning("Optional Hindsight recall runtime unavailable (configuration or client lifecycle)")
        finally:
            with self._lock:
                self._accepting = False
                self._stopping = True
                for record in self._records:
                    self._abandon(record)
                self._records.clear()
            self._started.set()

    async def _own_client(self) -> None:
        with self._lock:
            self._loop = asyncio.get_running_loop()
            self._stop = asyncio.Event()
        try:
            self._client = self._factory(
                base_url=self._base_url,
                api_key=self._api_key,
                timeout=self._timeout,
                max_attempts=1,
                user_agent="SillyTavernTelegramBridge/1.0",
            )
            with self._lock:
                self._accepting = not self._stopping
                if self._stopping:
                    self._stop.set()
            self._started.set()
            await self._stop.wait()
        finally:
            with self._lock:
                self._accepting = False
                records = tuple(self._records)
                for record in records:
                    self._abandon(record)
            tasks = [record.task for record in records if record.task is not None]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if self._client is not None:
                await self._client.aclose()
                self._client_closed = True

    def recall(self, *, bank_id: str, session_id: str, query: str, max_tokens: int) -> RecallIdentities:
        deadline = self._clock() + self._timeout
        if (
            not bank_id
            or len(bank_id) > 256
            or not session_id
            or len(session_id) > 256
            or not query.strip()
            or not isinstance(max_tokens, int)
            or max_tokens <= 0
        ):
            return ()
        with self._lock:
            if not self._accepting or self._loop is None or len(self._records) >= self._capacity:
                return ()
            if self._open_until and (self._clock() < self._open_until or self._probe_in_flight):
                return ()
            probe = bool(self._open_until)
            record = _Request(bank_id, session_id, query[:4000], max_tokens, deadline, self._generation, probe)
            self._records.add(record)
            self._probe_in_flight = self._probe_in_flight or probe
            self._loop.call_soon_threadsafe(self._install, record)
        try:
            return record.completion.result(timeout=max(0.0, deadline - self._clock()))
        except TimeoutError:
            with self._lock:
                self._abandon(record)
                failure = self._health(record, "availability") if not self._stopping else None
                self._schedule_cancel(record)
            self._log_failure(failure)
            return ()

    def _install(self, record: _Request) -> None:
        with self._lock:
            if record.abandoned or not self._accepting:
                self._records.discard(record)
                self._abandon(record)
                return
            record.task = asyncio.create_task(self._request(record))
            record.task.add_done_callback(lambda task: self._finished(record, task))

    async def _request(self, record: _Request) -> tuple[RecallIdentities, Health]:
        try:
            remaining = record.deadline - self._clock()
            if remaining <= 0:
                return (), "availability"
            async with asyncio.timeout(remaining):
                result = await self._client.arecall(
                    bank_id=record.bank_id,
                    query=record.query,
                    max_tokens=record.max_tokens,
                    budget="low",
                    tags=[f"session:{record.session_id}", "native-fact"],
                    tags_match="all_strict",
                    types=["world", "experience"],
                    include_entities=False,
                    include_chunks=False,
                    include_source_facts=False,
                    prefer_observations=False,
                )
                return _identities(result), "success"
        except asyncio.CancelledError:
            return (), "cancelled"
        except Exception as exc:
            return (), _failure_kind(exc)

    def _finished(self, record: _Request, task: asyncio.Task[tuple[RecallIdentities, Health]]) -> None:
        identities, outcome = ((), "cancelled") if task.cancelled() else task.result()
        failure = None
        with self._lock:
            self._records.discard(record)
            if not record.abandoned and self._accepting:
                if self._clock() >= record.deadline:
                    identities, outcome = (), "availability"
                failure = self._health(record, outcome)
            else:
                identities = ()
            if record.probe and record.generation == self._generation:
                self._probe_in_flight = False
            if not record.completion.done():
                record.completion.set_result(identities)
        self._log_failure(failure)

    def _health(self, record: _Request, outcome: Health) -> Health | None:
        """Called under the state lock; report once and fence superseded requests."""
        if record.health_reported:
            return None
        record.health_reported = True
        if record.generation != self._generation or outcome == "cancelled":
            return None
        if outcome == "success":
            if record.probe:
                self._open_until = 0.0
                self._probe_in_flight = False
                self._generation += 1
            return None
        self._generation += 1
        self._open_until = self._clock() + self._cooldown
        self._probe_in_flight = False
        return outcome

    @staticmethod
    def _log_failure(failure: Health | None) -> None:
        if failure is not None:
            logging.warning("Optional Hindsight recall unavailable (%s); cooldown active", failure)

    @staticmethod
    def _abandon(record: _Request) -> None:
        record.abandoned = True
        if not record.completion.done():
            record.completion.set_result(())

    def _schedule_cancel(self, record: _Request) -> None:
        if self._loop is not None and not self._loop.is_closed() and record.task is not None:
            self._loop.call_soon_threadsafe(record.task.cancel)

    def begin_shutdown(self) -> None:
        with self._lock:
            if self._stopping:
                return
            self._stopping = True
            self._accepting = False
            for record in self._records:
                self._abandon(record)
                self._schedule_cancel(record)
            if self._loop is not None and not self._loop.is_closed() and self._stop is not None:
                self._loop.call_soon_threadsafe(self._stop.set)

    def close(self, *, timeout: float = RECALL_SHUTDOWN_SECONDS) -> bool:
        self.begin_shutdown()
        thread = self._thread
        if thread is None:
            return True
        if thread is not threading.current_thread():
            thread.join(timeout=max(0.0, timeout))
        if thread.is_alive():
            logging.warning("Optional Hindsight recall owner did not close before shutdown deadline")
            return False
        return self._client is None or self._client_closed
