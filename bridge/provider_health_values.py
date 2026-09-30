"""Immutable, content-free runtime health values shared by policy and diagnostics."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HealthSnapshot:
    provider_id: str
    model_id: str = ""
    state: str = "unknown"
    consecutive_failures: int = 0
    last_success_at: float | None = None
    last_failure_at: float | None = None
    last_category: str | None = None
    last_status: int | None = None
    cooldown_until: float = 0.0
    backoff_step: int = 0
    revision: int = 0


@dataclass(frozen=True)
class HealthAttempt:
    provider_id: str
    model_id: str
    token: int
    provider_revision: int
    model_revision: int

    @property
    def selection(self) -> str:
        return f"{self.provider_id}::{self.model_id}"
