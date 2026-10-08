"""Low-level user action/dialogue normalization shared by prompt builders."""

from __future__ import annotations

import re

from bridge.narrative_values import NARRATIVE_STEERING_PREFIX


def format_user_dialogue_action(text: str) -> str:
    """Make user dialogue and single-star actions explicit to the model."""
    original = str(text or "").strip()
    if original.startswith(NARRATIVE_STEERING_PREFIX):
        return (
            "Out-of-world narrative steering, not character dialogue, action, knowledge, or a completed event:\n"
            + original[len(NARRATIVE_STEERING_PREFIX) :]
        )
    actions = re.findall(r"(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)", original, flags=re.DOTALL)
    if not actions:
        return original
    dialogue = re.sub(r"(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)", " ", original, flags=re.DOTALL)
    dialogue = re.sub(r"\s+", " ", dialogue).strip()
    action_text = " ".join(re.sub(r"\s+", " ", item).strip() for item in actions).strip()
    sections = []
    if dialogue:
        sections.append("User dialogue:\n" + dialogue)
    if action_text:
        sections.append("User action:\n" + action_text)
    return "\n\n".join(sections) or original
