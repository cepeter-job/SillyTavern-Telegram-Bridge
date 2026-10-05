"""Read-only guards for the actual, normalized provider request."""

from __future__ import annotations

from collections.abc import Callable

from bridge.context_compaction import ContextWindowBudgetError, budget_chat_messages
from bridge.settings import AppSettings


def check_attempt_budget(
    messages: list[dict],
    model: str,
    requested_output_tokens: int,
    *,
    transport: str,
    app_settings: AppSettings,
    context_observer: Callable[[dict[str, object]], None] | None = None,
    stage: str = "wire",
) -> None:
    """Validate without removing required content or changing transport retries."""
    try:
        _, stats = budget_chat_messages(
            messages,
            model,
            requested_output_tokens,
            app_settings=app_settings,
            compact=False,
            transport=transport,
        )
    except ContextWindowBudgetError as exc:
        exc.stats.update({"model": model[:160], "request_stage": stage})
        if context_observer is not None:
            context_observer(dict(exc.stats))
        raise
    stats.update({"model": model[:160], "request_stage": stage})
    if context_observer is not None:
        context_observer(stats)
