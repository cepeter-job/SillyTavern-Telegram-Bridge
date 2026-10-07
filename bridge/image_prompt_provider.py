"""Utility-provider boundary for current-scene image prompt preparation."""

from __future__ import annotations

import math

from bridge.provider_errors import ProviderRequestError
from bridge.provider_port import ProviderPort


class ImagePromptPreparationError(RuntimeError):
    """A Utility-model failure prevented current-scene image prompt preparation."""

    def __init__(self, provider_error: ProviderRequestError):
        super().__init__(str(provider_error))
        self.provider_error = provider_error


def generate_image_prompt_text(
    provider_port: ProviderPort,
    chat_id: str,
    session_id: str,
    api_key: str,
    model: str,
    messages: list[dict],
    *,
    settings: dict[str, object],
) -> str:
    """Generate prompt text through the Utility fallback policy and preserve stage identity."""
    try:
        return provider_port.for_usage(chat_id, session_id, "image_prompt").generate(
            api_key,
            model,
            messages,
            session_id=f"image-prompt:{chat_id}:{session_id}",
            settings=settings,
            force_non_stream=True,
        )
    except ProviderRequestError as exc:
        raise ImagePromptPreparationError(exc) from exc


def image_prompt_provider_error_message(error: ProviderRequestError) -> str:
    reasons = {
        "rate_limit": "Rate limited by provider",
        "authentication": "Provider authentication failed",
        "credits": "Provider requires credits",
        "model_unavailable": "Utility model unavailable",
        "timeout": "Provider request timed out",
        "provider_unavailable": "Provider temporarily unavailable",
        "network": "Provider could not be reached",
    }
    status = f" (HTTP {error.status})" if error.status is not None else ""
    lines = [
        "Image prompt preparation failed.",
        "",
        f"Utility model: {error.model}",
        f"Reason: {reasons.get(error.category, 'Provider request failed')}{status}",
    ]
    if error.retry_after is not None and error.retry_after > 0:
        lines.append(f"Retry: in {math.ceil(error.retry_after)} seconds")
    lines.extend(
        [
            "Image model: not called",
            "Next: Try again later or choose another Utility model in /provider.",
        ]
    )
    return "\n".join(lines)
