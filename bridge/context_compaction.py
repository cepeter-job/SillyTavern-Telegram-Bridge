"""Budget-aware prompt compaction for long-running sessions.

The compactor never rewrites the current user turn or the fixed character/system
instructions. It first drops oldest conversational history, then shrinks
retrieved RAG/memory context, then the continuity summary. If the fixed prompt
alone exceeds the configured budget it reports that condition rather than
silently truncating character instructions.
"""

from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass
from typing import cast

from bridge.codex_models import codex_context_window_tokens
from bridge.provider_catalog import context_metadata_for_model
from bridge.provider_errors import ProviderRequestError
from bridge.settings import DEFAULT_CONTEXT_INPUT_CAP_TOKENS, AppSettings

DEFAULT_CONTEXT_WINDOW_TOKENS = 32768
DEFAULT_CONTEXT_OUTPUT_RESERVE_TOKENS = 4096
DEFAULT_CONTEXT_HISTORY_CANDIDATES = 96
MIN_CONTEXT_INPUT_BUDGET_TOKENS = 2048
CONTEXT_SAFETY_MARGIN_RATIO = 0.02
MIN_CONTEXT_SAFETY_MARGIN_TOKENS = 512
MAX_CONTEXT_SAFETY_MARGIN_TOKENS = 8192
DEFAULT_TOKEN_ESTIMATE_CHARS_PER_TOKEN = 4.0


@dataclass(frozen=True)
class ContextProfile:
    window_tokens: int
    output_reserve_tokens: int
    safety_margin_tokens: int
    input_budget_tokens: int
    chars_per_token: float
    source: str
    requested_output_tokens: int = 0
    output_reservation_only: bool = False
    input_cap_tokens: int = DEFAULT_CONTEXT_INPUT_CAP_TOKENS
    input_budget_limiter: str = "model-window"


class ContextWindowBudgetError(ProviderRequestError):
    """Prompt cannot fit before any provider request is attempted."""

    def __init__(self, model: str, stats: dict[str, object]) -> None:
        self.stats = dict(stats)
        super().__init__(model or "the selected model", "request_too_large")

    def _user_message(self) -> str:
        final_tokens = int(cast(int, self.stats.get("final_tokens") or 0))
        budget_tokens = int(cast(int, self.stats.get("budget_tokens") or 0))
        window_tokens = int(cast(int, self.stats.get("window_tokens") or 0))
        if self.stats.get("input_budget_limiter") == "input-cap":
            return (
                f"Prompt for {self.model} exceeds the configured input cap "
                f"({final_tokens:,} estimated input tokens; {budget_tokens:,} allowed). "
                "Reduce fixed character/world/system instructions or the current message, "
                "or increase SILLYTAVERN_CONTEXT_INPUT_CAP_TOKENS."
            )
        return (
            f"Prompt for {self.model} cannot fit the configured context window "
            f"({final_tokens:,} estimated input tokens; {budget_tokens:,} usable of "
            f"{window_tokens:,}). Reduce fixed character/world/system instructions "
            "or choose a larger-context model."
        )


def normalize_token_ratio(value: object) -> float:
    try:
        ratio = float(cast(float, value))
    except (TypeError, ValueError):
        return DEFAULT_TOKEN_ESTIMATE_CHARS_PER_TOKEN
    return ratio if math.isfinite(ratio) and 1.0 <= ratio <= 8.0 else DEFAULT_TOKEN_ESTIMATE_CHARS_PER_TOKEN


def context_profile(
    model: str = "",
    *,
    app_settings: AppSettings,
    requested_output_tokens: int | None = None,
    transport: str = "",
) -> ContextProfile:
    codex_window = codex_context_window_tokens(model)
    metadata = context_metadata_for_model(model, app_settings=app_settings)
    if codex_window is not None:
        window = codex_window
        source = "codex-alias"
    else:
        window = int(cast(int, metadata.get("window_tokens") or app_settings.context_window_tokens))
        source = str(metadata.get("source") or "global-fallback")
    ratio = normalize_token_ratio(metadata.get("chars_per_token"))
    actual_transport = transport or str(metadata.get("transport") or "")
    safety = min(
        MAX_CONTEXT_SAFETY_MARGIN_TOKENS,
        max(MIN_CONTEXT_SAFETY_MARGIN_TOKENS, math.ceil(window * CONTEXT_SAFETY_MARGIN_RATIO)),
    )
    if requested_output_tokens is None:
        # Compatibility for UI/default profiles. Actual requests always supply
        # their output setting and must never silently reduce that reservation.
        reserve = min(
            int(app_settings.context_output_reserve_tokens),
            max(512, window - safety - MIN_CONTEXT_INPUT_BUDGET_TOKENS),
        )
        budget = max(MIN_CONTEXT_INPUT_BUDGET_TOKENS, window - reserve - safety)
        requested = reserve
    else:
        requested = int(requested_output_tokens)
        reserve = max(3000, requested) if actual_transport == "opencode_muse" else requested
        budget = window - reserve - safety
    cap = app_settings.context_input_cap_tokens
    limiter = "input-cap" if budget > cap else "model-window"
    budget = min(budget, cap)
    profile = ContextProfile(
        window,
        reserve,
        safety,
        budget,
        ratio,
        source,
        requested,
        actual_transport == "openai_codex" or codex_window is not None,
        input_cap_tokens=cap,
        input_budget_limiter=limiter,
    )
    if requested <= 0 or budget <= 0:
        stats = profile_stats(profile)
        stats.update({"original_tokens": 0, "final_tokens": 0, "over_budget": True, "allocation_invalid": True})
        raise ContextWindowBudgetError(model, stats)
    return profile


def profile_stats(profile: ContextProfile) -> dict[str, object]:
    return {
        "window_tokens": profile.window_tokens,
        "output_reserve_tokens": profile.output_reserve_tokens,
        "requested_output_tokens": profile.requested_output_tokens,
        "safety_margin_tokens": profile.safety_margin_tokens,
        "budget_tokens": max(0, profile.input_budget_tokens),
        "input_cap_tokens": profile.input_cap_tokens,
        "input_budget_limiter": profile.input_budget_limiter,
        "chars_per_token": profile.chars_per_token,
        "source": profile.source,
        "output_reservation_only": profile.output_reservation_only,
        "estimated": True,
    }


def context_window_tokens(model: str = "", *, app_settings: AppSettings) -> int:
    return context_profile(model, app_settings=app_settings).window_tokens


def context_output_reserve_tokens(*, app_settings: AppSettings) -> int:
    return app_settings.context_output_reserve_tokens


def context_input_budget_tokens(model: str = "", *, app_settings: AppSettings) -> int:
    return context_profile(model, app_settings=app_settings).input_budget_tokens


def context_history_candidate_limit(*, app_settings: AppSettings) -> int:
    return app_settings.context_history_candidates


def _content_tokens(content: object, chars_per_token: float) -> int:
    ratio = normalize_token_ratio(chars_per_token)
    if isinstance(content, str):
        return max(1, math.ceil(len(content) / ratio))
    if isinstance(content, list):
        total = 0
        for item in content:
            if not isinstance(item, dict):
                total += _content_tokens(str(item), ratio)
                continue
            if item.get("type") in {"image_url", "image", "input_image"}:
                # Do not count a base64 data URI as text. Reserve a conservative
                # fixed amount; providers account for image tokens differently.
                total += 1024
            else:
                total += _content_tokens(item.get("text") or "", ratio)
        return total
    return _content_tokens(str(content or ""), ratio)


def estimate_message_tokens(
    messages: list[dict], *, chars_per_token: float = DEFAULT_TOKEN_ESTIMATE_CHARS_PER_TOKEN
) -> int:
    return sum(8 + _content_tokens(message.get("content", ""), chars_per_token) for message in messages) + 16


def _replace_text_content(message: dict, text: str) -> None:
    content = message.get("content")
    if isinstance(content, str):
        message["content"] = text
        return
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                item["text"] = text
                return


def _text_content(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                return str(item.get("text") or "")
    return ""


def _trim_middle(value: str, maximum: int) -> str:
    value = str(value or "")
    if maximum <= 0:
        return ""
    if len(value) <= maximum:
        return value
    if maximum < 80:
        return value[:maximum]
    head = maximum * 2 // 3
    tail = maximum - head - 18
    return value[:head].rstrip() + "\n…[compacted]…\n" + value[-max(0, tail) :].lstrip()


_OPTIONAL_TAGS = {
    "rag": "untrusted_data_bank_references",
    "memory": "untrusted_memory",
    "episodic": "untrusted_episodic_memory",
    "npc": "untrusted_npc_state",
    "simulation": "untrusted_simulation_state",
}


def _optional_span(message: dict, kind: str) -> tuple[int, int] | None:
    text = _text_content(message)
    if "_context_optional" in message:
        # Assembly records exact body spans, including an empty list on fixed
        # messages. User-supplied lookalike tags/headings cannot become optional.
        for span in message["_context_optional"]:
            if span["kind"] == kind and 0 <= span["start"] <= span["end"] <= len(text):
                return int(span["start"]), int(span["end"])
        return None
    if kind == "summary":
        marker = "## Session continuity summary\n"
        start = text.find(marker)
        if start < 0:
            return None
        start += len(marker)
        end = text.find("\n\n## ", start)
        return start, len(text) if end < 0 else end
    tag = _OPTIONAL_TAGS[kind]
    match = re.search(rf"<{tag}>\n?(.*?)\n?</{tag}>", text, flags=re.DOTALL)
    return match.span(1) if match else None


def _shrink_optional(message: dict, kind: str, maximum: int) -> bool:
    span = _optional_span(message, kind)
    if span is None:
        return False
    start, end = span
    text = _text_content(message)
    trimmed = _trim_middle(text[start:end], maximum)
    if trimmed == text[start:end]:
        return False
    _replace_text_content(message, text[:start] + trimmed + text[end:])
    change = len(trimmed) - (end - start)
    for other in message.get("_context_optional", []):
        if other["kind"] == kind:
            other["end"] += change
        elif other["start"] >= end:
            other["start"] += change
            other["end"] += change
    return True


def _strip_context_metadata(messages: list[dict]) -> None:
    for message in messages:
        message.pop("_context_optional", None)


def compact_chat_messages(
    messages: list[dict],
    budget_tokens: int | None = None,
    *,
    min_recent_messages: int = 6,
    chars_per_token: float = DEFAULT_TOKEN_ESTIMATE_CHARS_PER_TOKEN,
    app_settings: AppSettings,
    preserve_last_assistant: bool = False,
) -> tuple[list[dict], dict[str, object]]:
    """Compact a built prompt while preserving fixed instructions/current turn."""
    budget = max(
        0, int(context_input_budget_tokens(app_settings=app_settings) if budget_tokens is None else budget_tokens)
    )
    chars_per_token = normalize_token_ratio(chars_per_token)
    compacted = copy.deepcopy(messages)
    original_tokens = estimate_message_tokens(compacted, chars_per_token=chars_per_token)
    dropped_history = 0
    rag_trimmed = memory_trimmed = npc_trimmed = summary_trimmed = False

    def current_tokens() -> int:
        return estimate_message_tokens(
            [message for message in compacted if not message.get("_drop_for_context")],
            chars_per_token=chars_per_token,
        )

    def drop_old_turns(floor: int) -> None:
        """Mark the oldest removable turns as dropped until budget or the floor."""
        nonlocal compacted, dropped_history
        conversation_indices = [
            index for index, message in enumerate(compacted) if message.get("role") in {"user", "assistant"}
        ]
        protected_latest = conversation_indices[-1] if conversation_indices else -1
        protected = {protected_latest}
        if preserve_last_assistant:
            last_assistant = next(
                (index for index in reversed(conversation_indices) if compacted[index].get("role") == "assistant"), -1
            )
            protected.add(last_assistant)
        removable = [index for index in conversation_indices if index not in protected]
        while current_tokens() > budget and len(removable) > floor:
            index = removable.pop(0)
            compacted[index]["_drop_for_context"] = True
            dropped_history += 1
        compacted = [message for message in compacted if not message.pop("_drop_for_context", False)]

    def shrink_user_tagged_sections(compute_target: bool, fallback_to_last: bool) -> None:
        nonlocal rag_trimmed, memory_trimmed, npc_trimmed
        latest = next(
            (message for message in reversed(compacted) if message.get("role") == "user"),
            compacted[-1] if fallback_to_last and compacted else None,
        )
        if latest is None:
            return
        for kind in ("rag", "memory", "episodic", "npc", "simulation"):
            if current_tokens() <= budget:
                break
            span = _optional_span(latest, kind)
            if span is None:
                continue
            deficit_chars = math.ceil(max(0, current_tokens() - budget) * chars_per_token)
            target = max(512, span[1] - span[0] - deficit_chars - 64) if compute_target else 0
            if _shrink_optional(latest, kind, target):
                if kind == "rag":
                    rag_trimmed = True
                elif kind == "npc":
                    npc_trimmed = True
                else:
                    memory_trimmed = True

    def shrink_summary(compute_target: bool) -> None:
        nonlocal summary_trimmed
        system_message = next((message for message in compacted if message.get("role") == "system"), None)
        if system_message is None:
            return
        span = _optional_span(system_message, "summary")
        if span is None:
            return
        deficit_chars = math.ceil(max(0, current_tokens() - budget) * chars_per_token)
        target = max(1000, span[1] - span[0] - deficit_chars - 64) if compute_target else 0
        if _shrink_optional(system_message, "summary", target):
            summary_trimmed = True

    if original_tokens <= budget:
        _strip_context_metadata(compacted)
        return compacted, {
            "budget_tokens": budget,
            "original_tokens": original_tokens,
            "final_tokens": original_tokens,
            "dropped_history": 0,
            "rag_trimmed": False,
            "memory_trimmed": False,
            "npc_trimmed": False,
            "summary_trimmed": False,
            "over_budget": False,
        }

    # Preserve all system messages and the newest non-system message. Drop old
    # user/assistant turns first, keeping a recent conversational floor.
    drop_old_turns(max(0, int(min_recent_messages) - 1))

    # Trim untrusted retrieved context before touching continuity summary.
    if current_tokens() > budget:
        shrink_user_tagged_sections(compute_target=True, fallback_to_last=True)

    # If still over budget, allow history to shrink to the latest pair.
    if current_tokens() > budget:
        drop_old_turns(1)

    # Continuity summary is valuable, so compact it only after history and
    # retrieval context have already been reduced.
    if current_tokens() > budget:
        shrink_summary(compute_target=True)

    # Exhaust optional retrieved context only if the prompt is still too large.
    if current_tokens() > budget:
        shrink_user_tagged_sections(compute_target=False, fallback_to_last=False)

    if current_tokens() > budget:
        shrink_summary(compute_target=False)

    # A recent-pair preference must not make otherwise optional old history
    # mandatory. Continuations explicitly protect their target assistant above.
    if current_tokens() > budget:
        drop_old_turns(0)

    final_tokens = current_tokens()
    _strip_context_metadata(compacted)
    return compacted, {
        "budget_tokens": budget,
        "original_tokens": original_tokens,
        "final_tokens": final_tokens,
        "dropped_history": dropped_history,
        "rag_trimmed": rag_trimmed,
        "memory_trimmed": memory_trimmed,
        "npc_trimmed": npc_trimmed,
        "summary_trimmed": summary_trimmed,
        "over_budget": final_tokens > budget,
    }


def budget_chat_messages(
    messages: list[dict],
    model: str,
    requested_output_tokens: int,
    *,
    app_settings: AppSettings,
    compact: bool = True,
    preserve_last_assistant: bool = False,
    transport: str = "",
) -> tuple[list[dict], dict[str, object]]:
    """Bound the final request; wire attempts recheck without silently editing.

    This is a configured character-ratio estimate with per-message overhead and
    a fixed 1,024-token image allowance, not a model tokenizer or an image-size
    guarantee. Codex output is locally reserved; its wire API exposes no cap.
    """
    try:
        profile = context_profile(
            model,
            requested_output_tokens=requested_output_tokens,
            transport=transport,
            app_settings=app_settings,
        )
    except ContextWindowBudgetError as exc:
        estimated = estimate_message_tokens(messages, chars_per_token=float(cast(float, exc.stats["chars_per_token"])))
        exc.stats.update({"original_tokens": estimated, "final_tokens": estimated})
        raise
    if compact:
        result, stats = compact_chat_messages(
            messages,
            budget_tokens=profile.input_budget_tokens,
            chars_per_token=profile.chars_per_token,
            app_settings=app_settings,
            preserve_last_assistant=preserve_last_assistant,
        )
    else:
        result = messages
        estimated = estimate_message_tokens(messages, chars_per_token=profile.chars_per_token)
        stats = {
            "original_tokens": estimated,
            "final_tokens": estimated,
            "over_budget": estimated > profile.input_budget_tokens,
        }
    stats.update(profile_stats(profile))
    if stats["over_budget"]:
        raise ContextWindowBudgetError(model, stats)
    return result, stats
