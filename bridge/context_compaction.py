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
from bridge.settings import AppSettings

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


class ContextWindowBudgetError(ProviderRequestError):
    """Prompt cannot fit before any provider request is attempted."""

    def __init__(self, model: str, stats: dict[str, object]) -> None:
        self.stats = dict(stats)
        super().__init__(model or "the selected model", "request_too_large")

    def _user_message(self) -> str:
        final_tokens = int(cast(int, self.stats.get("final_tokens") or 0))
        budget_tokens = int(cast(int, self.stats.get("budget_tokens") or 0))
        window_tokens = int(cast(int, self.stats.get("window_tokens") or 0))
        return (
            f"Prompt for {self.model} cannot fit the configured context window "
            f"({final_tokens:,} estimated input tokens; {budget_tokens:,} usable of "
            f"{window_tokens:,}). Reduce fixed character/world/system instructions "
            "or choose a larger-context model."
        )


def context_profile(model: str = "", *, app_settings: AppSettings) -> ContextProfile:
    codex_window = codex_context_window_tokens(model)
    metadata = {} if codex_window is not None else context_metadata_for_model(model, app_settings=app_settings)
    if codex_window is not None:
        window = codex_window
        source = "codex-alias"
    else:
        window = int(cast(int, metadata.get("window_tokens") or app_settings.context_window_tokens))
        source = str(metadata.get("source") or "global-fallback")
    chars_per_token = float(cast(float, metadata.get("chars_per_token") or DEFAULT_TOKEN_ESTIMATE_CHARS_PER_TOKEN))
    safety = min(
        MAX_CONTEXT_SAFETY_MARGIN_TOKENS,
        max(MIN_CONTEXT_SAFETY_MARGIN_TOKENS, math.ceil(window * CONTEXT_SAFETY_MARGIN_RATIO)),
    )
    reserve = min(
        int(app_settings.context_output_reserve_tokens),
        max(512, window - safety - MIN_CONTEXT_INPUT_BUDGET_TOKENS),
    )
    budget = max(MIN_CONTEXT_INPUT_BUDGET_TOKENS, window - reserve - safety)
    return ContextProfile(window, reserve, safety, budget, chars_per_token, source)


def context_window_tokens(model: str = "", *, app_settings: AppSettings) -> int:
    return context_profile(model, app_settings=app_settings).window_tokens


def context_output_reserve_tokens(*, app_settings: AppSettings) -> int:
    return app_settings.context_output_reserve_tokens


def context_input_budget_tokens(model: str = "", *, app_settings: AppSettings) -> int:
    return context_profile(model, app_settings=app_settings).input_budget_tokens


def context_history_candidate_limit(*, app_settings: AppSettings) -> int:
    return app_settings.context_history_candidates


def _content_tokens(content: object, chars_per_token: float) -> int:
    ratio = min(8.0, max(1.0, float(chars_per_token or DEFAULT_TOKEN_ESTIMATE_CHARS_PER_TOKEN)))
    if isinstance(content, str):
        return max(1, math.ceil(len(content) / ratio))
    if isinstance(content, list):
        total = 0
        for item in content:
            if not isinstance(item, dict):
                total += _content_tokens(str(item), ratio)
                continue
            if item.get("type") == "image_url":
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


def _shrink_tagged_section(text: str, tag: str, target_chars: int) -> tuple[str, bool]:
    pattern = re.compile(
        rf"(<{re.escape(tag)}>\n?)(.*?)(\n?</{re.escape(tag)}>)",
        flags=re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        return text, False
    body = match.group(2)
    trimmed = _trim_middle(body, max(0, int(target_chars)))
    if trimmed == body:
        return text, False
    replacement = match.group(1) + trimmed + match.group(3)
    return text[: match.start()] + replacement + text[match.end() :], True


def _shrink_summary_section(text: str, target_chars: int) -> tuple[str, bool]:
    marker = "## Session continuity summary\n"
    start = text.find(marker)
    if start < 0:
        return text, False
    body_start = start + len(marker)
    next_section = text.find("\n\n## ", body_start)
    body_end = len(text) if next_section < 0 else next_section
    body = text[body_start:body_end]
    trimmed = _trim_middle(body, max(0, int(target_chars)))
    if trimmed == body:
        return text, False
    return text[:body_start] + trimmed + text[body_end:], True


def compact_chat_messages(
    messages: list[dict],
    budget_tokens: int | None = None,
    *,
    min_recent_messages: int = 6,
    chars_per_token: float = DEFAULT_TOKEN_ESTIMATE_CHARS_PER_TOKEN,
    app_settings: AppSettings,
) -> tuple[list[dict], dict[str, object]]:
    """Compact a built prompt while preserving fixed instructions/current turn."""
    budget = max(
        MIN_CONTEXT_INPUT_BUDGET_TOKENS,
        int(budget_tokens or context_input_budget_tokens(app_settings=app_settings)),
    )
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
        removable = [index for index in conversation_indices if index != protected_latest]
        while current_tokens() > budget and len(removable) > floor:
            index = removable.pop(0)
            compacted[index]["_drop_for_context"] = True
            dropped_history += 1
        compacted = [message for message in compacted if not message.pop("_drop_for_context", False)]

    def shrink_user_tagged_sections(compute_target: bool, fallback_to_last: bool) -> None:
        """Shrink <untrusted_*> sections in the latest user message."""
        nonlocal rag_trimmed, memory_trimmed, npc_trimmed
        latest = next(
            (message for message in reversed(compacted) if message.get("role") == "user"),
            compacted[-1] if fallback_to_last and compacted else None,
        )
        if latest is None:
            return
        text = _text_content(latest)
        for tag in (
            "untrusted_data_bank_references",
            "untrusted_memory",
            "untrusted_episodic_memory",
            "untrusted_npc_state",
        ):
            if current_tokens() <= budget:
                break
            if compute_target:
                pattern = re.search(
                    rf"<{tag}>\n?(.*?)\n?</{tag}>",
                    text,
                    flags=re.DOTALL,
                )
                if not pattern:
                    continue
                body_len = len(pattern.group(1))
                deficit_chars = max(0, (current_tokens() - budget) * 4)
                target = max(512, body_len - deficit_chars - 256)
            else:
                target = 0
            text, changed = _shrink_tagged_section(text, tag, target)
            if changed:
                _replace_text_content(latest, text)
                if tag == "untrusted_data_bank_references":
                    rag_trimmed = True
                elif tag == "untrusted_npc_state":
                    npc_trimmed = True
                else:
                    memory_trimmed = True

    def shrink_summary(compute_target: bool) -> None:
        """Shrink the continuity summary section of the system message."""
        nonlocal summary_trimmed
        system_message = next(
            (message for message in compacted if message.get("role") == "system"),
            None,
        )
        if system_message is None:
            return
        text = _text_content(system_message)
        if compute_target:
            marker = "## Session continuity summary\n"
            start = text.find(marker)
            if start < 0:
                return
            body_start = start + len(marker)
            next_section = text.find("\n\n## ", body_start)
            body_end = len(text) if next_section < 0 else next_section
            deficit_chars = max(0, (current_tokens() - budget) * 4)
            target = max(1000, (body_end - body_start) - deficit_chars - 256)
        else:
            target = 0
        text, changed = _shrink_summary_section(text, target)
        if changed:
            _replace_text_content(system_message, text)
            summary_trimmed = True

    if original_tokens <= budget:
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

    final_tokens = current_tokens()
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
