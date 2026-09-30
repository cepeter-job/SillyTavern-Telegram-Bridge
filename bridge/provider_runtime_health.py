"""Thread-safe observations of real provider requests; never performs network I/O."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import replace

from bridge.provider_errors import ProviderRequestError
from bridge.provider_health_values import HealthAttempt, HealthSnapshot

_TRANSIENT = frozenset({"timeout", "network", "provider_unavailable"})
_ACTION_STATES = {
    "authentication": "auth_error",
    "credits": "credits_required",
    "rate_limit": "rate_limited",
    "model_unavailable": "model_unavailable",
}


class ProviderRuntimeHealth:
    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        self._states: dict[tuple[str, str], HealthSnapshot] = {}
        self._active: dict[int, HealthAttempt] = {}
        self._next_token = 0

    def _get(self, provider_id: str, model_id: str = "") -> HealthSnapshot:
        return self._states.get((provider_id, model_id), HealthSnapshot(provider_id, model_id))

    def snapshot(self, provider_id: str, model_id: str = "") -> HealthSnapshot:
        with self._lock:
            return self._get(provider_id, model_id)

    def begin(self, provider_id: str, model_id: str) -> HealthAttempt:
        with self._lock:
            self._next_token += 1
            attempt = HealthAttempt(
                provider_id,
                model_id,
                self._next_token,
                self._get(provider_id).revision,
                self._get(provider_id, model_id).revision,
            )
            self._active[attempt.token] = attempt
            return attempt

    def _finish(self, attempt: HealthAttempt) -> bool:
        if self._active.get(attempt.token) != attempt:
            return False
        del self._active[attempt.token]
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
                self._states[(attempt.provider_id, model_id)] = updated

    def fail(self, attempt: HealthAttempt, error: ProviderRequestError) -> None:
        with self._lock:
            if not self._finish(attempt):
                return
            category = error.category
            if category not in _TRANSIENT and category not in _ACTION_STATES:
                return
            model_id = attempt.model_id if category == "model_unavailable" else ""
            snapshot = self._get(attempt.provider_id, model_id)
            self._states[(attempt.provider_id, model_id)] = replace(
                snapshot,
                state=_ACTION_STATES.get(category, "degraded"),
                consecutive_failures=snapshot.consecutive_failures + 1,
                last_failure_at=self._clock(),
                last_category=category,
                last_status=error.status,
                revision=snapshot.revision + 1,
            )
