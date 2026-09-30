"""Explicit policy above the deterministic router and provider transport."""

from __future__ import annotations

from dataclasses import dataclass

from bridge.model_router import ModelRouter
from bridge.provider_errors import ProviderRequestError
from bridge.provider_health_values import HealthAttempt
from bridge.provider_runtime_health import ProviderRuntimeHealth


@dataclass(frozen=True)
class ProviderExecutionPolicy:
    model_router: ModelRouter
    health: ProviderRuntimeHealth

    def candidates(self, model: str, purpose: str) -> tuple[str, ...]:
        route = self.model_router.route(model)
        return (f"{route.provider_id}::{route.model_id}",)

    def begin(self, model: str) -> HealthAttempt:
        route = self.model_router.route(model)
        return self.health.begin(route.provider_id, route.model_id)

    def succeed(self, attempt: HealthAttempt) -> None:
        self.health.succeed(attempt)

    def fail(self, attempt: HealthAttempt, error: ProviderRequestError) -> None:
        self.health.fail(attempt, error)

    def cancel(self, attempt: HealthAttempt) -> None:
        self.health.cancel(attempt)
