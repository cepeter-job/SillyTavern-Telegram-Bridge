"""Thread-safe observations of real provider requests; never performs network I/O."""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Protocol

from bridge.provider_errors import ProviderRequestError
from bridge.provider_health_values import HealthAttempt, HealthRecord, HealthSnapshot, HealthTransition

_TRANSIENT = frozenset({"timeout", "network", "provider_unavailable"})
_ACTION_STATES = {
    "authentication": "auth_error",
    "credits": "credits_required",
    "rate_limit": "rate_limited",
    "model_unavailable": "model_unavailable",
}


class HealthStore(Protocol):
    def load(self) -> tuple[HealthRecord, ...]: ...

    def save(self, records: tuple[HealthRecord, ...]) -> None: ...


class ProviderRuntimeHealth:
    def __init__(self, *, clock: Callable[[], float] = time.time, store: HealthStore | None = None) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        self._states: dict[tuple[str, str], HealthSnapshot] = {}
        self._active: dict[int, HealthAttempt] = {}
        self._next_token = 0
        self._probes: dict[tuple[str, str], int] = {}
        self._store = store
        self._histories: dict[tuple[str, str], tuple[HealthTransition, ...]] = {}
        self._updated: dict[tuple[str, str], float] = {}
        if store is not None:
            try:
                for record in store.load():
                    key = (record.snapshot.provider_id, record.snapshot.model_id)
                    self._states[key] = record.snapshot
                    self._histories[key] = record.history[-10:]
                    self._updated[key] = record.updated_at
            except Exception:
                logging.warning("Could not restore provider runtime observations")
        self._prune()

    def _prune(self) -> None:
        now = self._clock()
        expired = {key for key, stamp in self._updated.items() if now - stamp >= 86400}
        remaining = sorted((key for key in self._updated if key not in expired), key=self._updated.__getitem__)
        expired.update(remaining[: max(0, len(remaining) - 1024)])
        for attempt in tuple(self._active.values()):
            if (attempt.provider_id, "") in expired or (attempt.provider_id, attempt.model_id) in expired:
                self._finish(attempt)
        for key in expired:
            self._states.pop(key, None)
            self._histories.pop(key, None)
            self._updated.pop(key, None)
        for key, events in self._histories.items():
            self._histories[key] = tuple(event for event in events if now - event.at < 86400)

    def _record(self, key: tuple[str, str], state: HealthSnapshot, now: float) -> None:
        self._states[key] = state
        self._updated[key] = now
        event = HealthTransition(
            now,
            state.state,
            state.last_category if state.state not in {"healthy", "unknown"} else None,
            state.last_status if state.state not in {"healthy", "unknown"} else None,
        )
        self._histories[key] = (*self._histories.get(key, ()), event)[-10:]

    def records(self) -> tuple[HealthRecord, ...]:
        with self._lock:
            self._prune()
            return tuple(
                HealthRecord(state, self._histories.get(key, ()), self._updated[key])
                for key, state in self._states.items()
            )

    def history(self, provider_id: str, model_id: str = "") -> tuple[HealthTransition, ...]:
        with self._lock:
            self._prune()
            return self._histories.get((provider_id, model_id), ())

    def _persist(self) -> None:
        self._prune()
        if self._store is not None:
            try:
                self._store.save(self.records())
            except Exception:
                logging.warning("Could not persist provider runtime observations")

    def _get(self, provider_id: str, model_id: str = "") -> HealthSnapshot:
        return self._states.get((provider_id, model_id), HealthSnapshot(provider_id, model_id))

    def snapshot(self, provider_id: str, model_id: str = "") -> HealthSnapshot:
        with self._lock:
            self._prune()
            snapshot = self._get(provider_id, model_id)
            return replace(snapshot, state="half_open") if (provider_id, model_id) in self._probes else snapshot

    def reset(self, provider_id: str) -> None:
        """Clear local blocks only; invalidate pre-reset in-flight observations."""
        with self._lock:
            self._prune()
            for attempt in tuple(self._active.values()):
                if attempt.provider_id == provider_id:
                    self._finish(attempt)
            keys = {key for key in self._states if key[0] == provider_id} | {(provider_id, "")}
            now = self._clock()
            for key in keys:
                previous = self._get(*key)
                self._record(
                    key,
                    replace(
                        previous,
                        state="unknown",
                        consecutive_failures=0,
                        cooldown_until=0,
                        backoff_step=0,
                        revision=previous.revision + 1,
                    ),
                    now,
                )
            self._persist()

    def begin(self, provider_id: str, model_id: str) -> HealthAttempt:
        with self._lock:
            self._prune()
            now = self._clock()
            probe_keys = []
            for key in ((provider_id, ""), (provider_id, model_id)):
                snapshot = self._get(*key)
                if key in self._probes or snapshot.cooldown_until > now:
                    raise ProviderRequestError(
                        f"{provider_id}::{model_id}",
                        snapshot.last_category or "provider_unavailable",
                        snapshot.last_status,
                        retry_after=max(0, math.ceil(snapshot.cooldown_until - now)),
                        blocked=True,
                    )
                if snapshot.state in {
                    "cooldown",
                    "rate_limited",
                    "auth_error",
                    "credits_required",
                    "model_unavailable",
                }:
                    probe_keys.append(key)
            self._next_token += 1
            attempt = HealthAttempt(
                provider_id,
                model_id,
                self._next_token,
                self._get(provider_id).revision,
                self._get(provider_id, model_id).revision,
                tuple(probe_keys),
            )
            self._active[attempt.token] = attempt
            for key in probe_keys:
                self._probes[key] = attempt.token
            return attempt

    def _finish(self, attempt: HealthAttempt) -> bool:
        if self._active.get(attempt.token) != attempt:
            return False
        del self._active[attempt.token]
        for key in attempt.probe_keys:
            if self._probes.get(key) == attempt.token:
                del self._probes[key]
        return True

    def cancel(self, attempt: HealthAttempt) -> None:
        with self._lock:
            self._finish(attempt)

    def succeed(self, attempt: HealthAttempt) -> None:
        with self._lock:
            if not self._finish(attempt):
                return
            now = self._clock()
            for model_id, revision in (("", attempt.provider_revision), (attempt.model_id, attempt.model_revision)):
                snapshot = self._get(attempt.provider_id, model_id)
                updated = replace(snapshot, last_success_at=now)
                # An older in-flight success is evidence, not permission to clear a newer failure.
                if snapshot.revision == revision:
                    updated = replace(
                        updated, state="healthy", consecutive_failures=0, cooldown_until=0, backoff_step=0
                    )
                self._record((attempt.provider_id, model_id), updated, now)
            self._persist()

    def fail(self, attempt: HealthAttempt, error: ProviderRequestError) -> None:
        with self._lock:
            if not self._finish(attempt):
                return
            category = error.category
            if category not in _TRANSIENT and category not in _ACTION_STATES:
                return
            model_id = attempt.model_id if category == "model_unavailable" else ""
            key = (attempt.provider_id, model_id)
            snapshot = self._get(*key)
            revision = attempt.model_revision if model_id else attempt.provider_revision
            # A late timeout must not downgrade a newer auth/credit/rate-limit block.
            if (
                revision < snapshot.revision
                and snapshot.state in _ACTION_STATES.values()
                and _ACTION_STATES.get(category) != snapshot.state
            ):
                return
            now = self._clock()
            failures = snapshot.consecutive_failures + 1
            state = _ACTION_STATES.get(category, "degraded")
            backoff_step = snapshot.backoff_step
            cooldown = snapshot.cooldown_until
            if category in {"authentication", "credits", "model_unavailable"}:
                cooldown = max(cooldown, now + 300)
            elif category == "rate_limit":
                cooldown = max(cooldown, now + (60 if error.retry_after is None else error.retry_after))
            elif failures >= 3 or error.retry_after is not None:
                state = "cooldown"
                backoff_step = min(5, backoff_step + 1) if key in attempt.probe_keys else max(1, backoff_step)
                delay = min(900, 60 * 2 ** (backoff_step - 1))
                if error.retry_after is not None:
                    delay = max(delay, error.retry_after)
                cooldown = max(cooldown, now + delay)
            updated = replace(
                snapshot,
                state=state,
                consecutive_failures=failures,
                last_failure_at=now,
                last_category=category,
                last_status=error.status,
                cooldown_until=cooldown,
                backoff_step=backoff_step,
                revision=snapshot.revision + 1,
            )
            self._record(key, updated, now)
            self._persist()
