"""Synthetic native-builder observations; no production data, keys or model calls."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from bridge.card_content import card_fields
from bridge.generation import build_chat_messages
from bridge.settings import load_app_settings


class _NoPersona:
    def name(self, _persona: str) -> str:
        return ""

    def get(self, _persona: str) -> None:
        return None


def demo_records(directory: Path) -> Iterator[dict]:
    settings = load_app_settings({}, home=directory)
    fields = card_fields(
        {
            "name": "Rowan",
            "description": "A patient station keeper who respects the user's decisions. " * 8,
            "system_prompt": "Keep user agency and unknown character knowledge intact.",
            "post_history_instructions": "Continue in character without choosing the user's actions.",
        },
        app_settings=settings,
    )
    session = {"persona_id": "", "world_file": "", "model_id": "synthetic", "author_note": "Respect commitments."}
    for variant in ("stable", "changing_summary", "duplicate_instruction_control"):
        history = []
        for index in range(4):
            summary = (
                f"The lantern was inspected at step {index}."
                if variant == "changing_summary"
                else "The gate is closed."
            )
            messages = build_chat_messages(
                session,
                fields,
                f"Describe display {index} without choosing for me.",
                list(history),
                persona_service=_NoPersona(),
                session_summary=summary,
                scene_context="Rowan remains at the station.",
                app_settings=settings,
                defer_compaction=True,
            )
            if variant == "duplicate_instruction_control":
                # Deliberately artificial: the native builder does not accumulate
                # a repeated system footer on every stored dialogue turn.
                messages[1:1] = [{"role": "system", "content": "Use the required output format."} for _ in range(2)]
            yield {
                "scope": {
                    "session": variant,
                    "character": "synthetic-card",
                    "reader": "synthetic-reader",
                    "branch": "synthetic-branch",
                    "provider": "no-provider",
                    "model": "synthetic",
                    "stage": "builder",
                },
                "request": {"model": "synthetic", "messages": messages},
            }
            history.extend(
                [("user", f"I inspect display {index}."), ("assistant", f"Rowan describes display {index}.")]
            )
