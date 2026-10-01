"""Explicit policy above the deterministic router and provider transport."""

from __future__ import annotations

from dataclasses import dataclass

from bridge.model_router import ModelRouter, ModelRoutingError
from bridge.provider_errors import ProviderRequestError
from bridge.provider_health_values import HealthAttempt, HealthSnapshot, HealthTransition
from bridge.provider_runtime_health import ProviderRuntimeHealth


@dataclass(frozen=True)
class ProviderExecutionPolicy:
    model_router: ModelRouter
    health: ProviderRuntimeHealth

    def snapshot(self, provider_id: str, model_id: str = "") -> HealthSnapshot:
        return self.health.snapshot(provider_id, model_id)

    def history(self, provider_id: str, model_id: str = "") -> tuple[HealthTransition, ...]:
        return self.health.history(provider_id, model_id)

    def reset(self, provider_id: str) -> None:
        self.health.reset(provider_id)

    def candidates(self, model: str, purpose: str) -> tuple[str, ...]:
        route = self.model_router.route(model)
        choices = [f"{route.provider_id}::{route.model_id}"]
        if purpose in {"summary", "memory", "scene", "rank", "optimizer", "choices", "npc"}:
            raw = route.spec.get("utility_fallbacks")
        elif route.spec.get("allow_story_fallback") is True:
            raw = route.spec.get("story_fallbacks")
        else:
            raw = None
        if not isinstance(raw, (list, tuple)):
            return tuple(choices)
        for candidate in raw:
            if not isinstance(candidate, str) or "::" not in candidate or candidate in choices:
                continue
            try:
                resolved = self.model_router.route(candidate)
            except ModelRoutingError:
                continue
            choices.append(f"{resolved.provider_id}::{resolved.model_id}")
            if len(choices) >= 3:
                break
        return tuple(choices)

    def begin(self, model: str) -> HealthAttempt:
        route = self.model_router.route(model)
        return self.health.begin(route.provider_id, route.model_id)

    def succeed(self, attempt: HealthAttempt) -> None:
        self.health.succeed(attempt)

    def fail(self, attempt: HealthAttempt, error: ProviderRequestError) -> None:
        self.health.fail(attempt, error)

    def cancel(self, attempt: HealthAttempt) -> None:
        self.health.cancel(attempt)
