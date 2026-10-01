"""Versioned, bounded and private storage for sanitized provider observations."""

from __future__ import annotations

import json
import math
import os
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from bridge.provider_health_values import HealthRecord, HealthSnapshot, HealthTransition

MAX_ENTRIES = 1024
MAX_EVENTS = 10
MAX_BYTES = 4_000_000
RETENTION_SECONDS = 86400
_STATES = frozenset(
    {
        "unknown",
        "healthy",
        "degraded",
        "cooldown",
        "rate_limited",
        "auth_error",
        "credits_required",
        "model_unavailable",
    }
)
_CATEGORIES = frozenset(
    {
        "rate_limit",
        "authentication",
        "credits",
        "model_unavailable",
        "request_too_large",
        "timeout",
        "provider_unavailable",
        "network",
        "provider_rejected",
        "provider_failure",
    }
)


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("invalid numeric observation")
    try:
        result = float(value)
    except OverflowError:
        raise ValueError("invalid numeric observation") from None
    if not math.isfinite(result) or result < 0:
        raise ValueError("invalid numeric observation")
    return result


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**63 - 1:
        raise ValueError("invalid observation counter")
    return value


def _identity(value: object, *, optional: bool = False) -> str:
    if not isinstance(value, str) or len(value) > 200 or (not value and not optional):
        raise ValueError("invalid observation identity")
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("invalid observation identity")
    return value


def _state(value: object) -> str:
    if not isinstance(value, str) or value not in _STATES:
        raise ValueError("invalid observation state")
    return value


def _category(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in _CATEGORIES:
        raise ValueError("invalid observation category")
    return value


def _status(value: object) -> int | None:
    if value is None:
        return None
    status = _integer(value)
    if not 100 <= status <= 599:
        raise ValueError("invalid HTTP observation")
    return status


def _optional_time(value: object, now: float) -> float | None:
    if value is None:
        return None
    result = _number(value)
    if result > now + 300:
        raise ValueError("future observation")
    return result


def _record(raw: object, now: float) -> HealthRecord | None:
    if not isinstance(raw, dict) or set(raw) != {"snapshot", "history", "updated_at"}:
        return None
    try:
        updated = _number(raw["updated_at"])
        if updated > now + 300 or now - updated >= RETENTION_SECONDS:
            return None
        state = raw["snapshot"]
        events = raw["history"]
        if not isinstance(state, dict) or set(state) != set(HealthSnapshot.__dataclass_fields__):
            return None
        if not isinstance(events, list) or len(events) > MAX_EVENTS:
            return None
        cooldown = _number(state["cooldown_until"])
        if cooldown > now + RETENTION_SECONDS:
            return None
        snapshot = HealthSnapshot(
            provider_id=_identity(state["provider_id"]),
            model_id=_identity(state["model_id"], optional=True),
            state=_state(state["state"]),
            consecutive_failures=_integer(state["consecutive_failures"]),
            last_success_at=_optional_time(state["last_success_at"], now),
            last_failure_at=_optional_time(state["last_failure_at"], now),
            last_category=_category(state["last_category"]),
            last_status=_status(state["last_status"]),
            cooldown_until=cooldown,
            backoff_step=_integer(state["backoff_step"]),
            revision=_integer(state["revision"]),
        )
        history = []
        for event in events:
            if not isinstance(event, dict) or set(event) != {"at", "state", "category", "status"}:
                return None
            at = _number(event["at"])
            if at > now + 300:
                return None
            transition = HealthTransition(
                at, _state(event["state"]), _category(event["category"]), _status(event["status"])
            )
            if now - at < RETENTION_SECONDS:
                history.append(transition)
        return HealthRecord(snapshot, tuple(history), updated)
    except (ValueError, TypeError, KeyError):
        return None


class JsonProviderHealthStore:
    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self._clock = clock

    def load(self) -> tuple[HealthRecord, ...]:
        try:
            with self.path.open("rb") as source:
                content = source.read(MAX_BYTES + 1)
            if len(content) > MAX_BYTES:
                return ()
            value = json.loads(content)
        except (OSError, ValueError):
            return ()
        if not isinstance(value, dict) or value.get("version") != 1 or isinstance(value.get("version"), bool):
            return ()
        rows = value.get("records")
        if not isinstance(rows, list) or len(rows) > MAX_ENTRIES:
            return ()
        now = self._clock()
        records: dict[tuple[str, str], HealthRecord] = {}
        for raw in rows:
            record = _record(raw, now)
            if record is not None:
                key = (record.snapshot.provider_id, record.snapshot.model_id)
                previous = records.get(key)
                if previous is None or record.updated_at >= previous.updated_at:
                    records[key] = record
        return tuple(records.values())

    def save(self, records: tuple[HealthRecord, ...]) -> None:
        selected = sorted(records, key=lambda item: item.updated_at, reverse=True)[:MAX_ENTRIES]
        payload = {"version": 1, "records": [asdict(record) for record in selected]}
        data = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if len(data) > MAX_BYTES:
            raise ValueError("provider diagnostics exceed storage limit")
        temporary: str | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.path.parent, prefix=".provider-health-", delete=False) as output:
                temporary = output.name
                os.chmod(temporary, 0o600)
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            temporary = None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
