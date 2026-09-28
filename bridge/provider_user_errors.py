"""Presentation-safe messages for canonical provider-request failures."""

from __future__ import annotations

from bridge.port_contracts import ProviderRequestError


class ProviderUserError(RuntimeError):
    """A provider failure whose message is safe to show to an end user."""


def actionable_provider_user_error(error: BaseException) -> ProviderUserError | None:
    """Render only canonical provider failures; unrelated application errors remain generic."""
    if not isinstance(error, ProviderRequestError):
        return None
    return provider_user_error(error)


def provider_user_error(error: ProviderRequestError) -> ProviderUserError:
    selected = error.model
    status = error.status
    if error.kind == "rate_limit":
        suffix = f" (HTTP {status})" if status else ""
        message = f"Model {selected} is rate-limited{suffix}. Try again later or choose another model."
    elif error.kind == "authentication":
        suffix = f" (HTTP {status})" if status else ""
        message = f"Model {selected} was rejected by the provider{suffix}. "
        message += "Check its credentials or choose another model."
    elif error.kind == "credits_required":
        suffix = f" (HTTP {status})" if status else ""
        message = f"The provider requires credits for {selected}{suffix}. Add credits or choose another model."
    elif error.kind == "model_unavailable":
        suffix = f" (HTTP {status})" if status else ""
        message = f"Model {selected} is unavailable{suffix}. Refresh providers or choose another model."
    elif error.kind == "request_too_large":
        suffix = f" (HTTP {status})" if status else ""
        message = f"The request for {selected} is too large{suffix}. Start a shorter session or choose another model."
    elif error.kind == "timeout":
        suffix = f" (HTTP {status})" if status else ""
        message = f"Model {selected} timed out{suffix}. Retry or choose another model."
    elif error.kind == "provider_unavailable":
        suffix = f" (HTTP {status})" if status else ""
        message = f"The provider for {selected} is temporarily unavailable{suffix}. Try again later."
    elif error.kind == "connection":
        message = f"The provider for {selected} could not be reached. Retry or choose another model."
    else:
        suffix = f" (HTTP {status})" if status else ""
        message = f"The provider rejected {selected}{suffix}. Retry or choose another model."
    return ProviderUserError(message)
