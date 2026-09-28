"""Canonical, sanitized provider failure types shared across transports and application surfaces."""

from __future__ import annotations

_CATEGORIES = {
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


def _safe_model_name(model: str) -> str:
    value = str(model or "").strip()
    if not value or len(value) > 200 or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        return "the selected model"
    return value


def provider_category_for_status(status: int) -> str:
    if status == 429:
        return "rate_limit"
    if status in {401, 403}:
        return "authentication"
    if status == 402:
        return "credits"
    if status == 404:
        return "model_unavailable"
    if status == 413:
        return "request_too_large"
    if status in {408, 504}:
        return "timeout"
    if 500 <= status <= 599:
        return "provider_unavailable"
    return "provider_rejected"


class ProviderTransportError(RuntimeError):
    """Sanitized transport failure without user/model identity."""

    def __init__(self, category: str, status: int | None = None) -> None:
        if category not in _CATEGORIES:
            raise ValueError("invalid provider failure category")
        self.category = category
        self.status = int(status) if status is not None else None
        super().__init__("provider transport failed")


class ProviderRequestError(RuntimeError):
    """Sanitized provider failure bound to the selected model."""

    def __init__(self, model: str, category: str, status: int | None = None) -> None:
        if category not in _CATEGORIES:
            raise ValueError("invalid provider failure category")
        self.model = _safe_model_name(model)
        self.category = category
        self.status = int(status) if status is not None else None
        super().__init__(self._user_message())

    def _user_message(self) -> str:
        model = self.model
        status = self.status
        suffix = f" (HTTP {status})" if status is not None else ""
        if self.category == "rate_limit":
            return f"Model {model} is rate-limited{suffix}. Try again later or choose another model."
        if self.category == "authentication":
            return f"Model {model} was rejected by the provider{suffix}. Check its credentials or choose another model."
        if self.category == "credits":
            return f"The provider requires credits for {model}{suffix}. Add credits or choose another model."
        if self.category == "model_unavailable":
            return f"Model {model} is unavailable{suffix}. Refresh providers or choose another model."
        if self.category == "request_too_large":
            return f"The request for {model} is too large{suffix}. Start a shorter session or choose another model."
        if self.category == "timeout":
            return f"Model {model} timed out{suffix}. Retry or choose another model."
        if self.category == "provider_unavailable":
            return f"The provider for {model} is temporarily unavailable{suffix}. Try again later."
        if self.category == "network":
            return f"The provider for {model} could not be reached. Retry or choose another model."
        if self.category == "provider_rejected":
            return f"The provider rejected {model}{suffix}. Retry or choose another model."
        return f"Model {model} failed. Retry or choose another model."

    @property
    def miniapp_status(self) -> int:
        return 429 if self.category == "rate_limit" else 502

    @property
    def miniapp_code(self) -> str:
        return "provider_rate_limited" if self.category == "rate_limit" else "provider_error"
