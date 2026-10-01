"""Bounded Light Novel protocol, separate from visible story prose."""

from __future__ import annotations

import json
import re
import unicodedata

from bridge.telegram_output import telegram_safe_output

MAX_CHOICE_CHARS = 160
MAX_RESPONSE_CHARS = 96000


def validate_choices(value: object, requested_count: int) -> list[str]:
    if requested_count not in {2, 3, 4} or not isinstance(value, list) or len(value) != requested_count:
        raise ValueError("Expected exactly the requested 2–4 choices")
    choices = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("Choices must be text")
        text = " ".join(telegram_safe_output(item).split())
        if not text or len(text) > MAX_CHOICE_CHARS or text.startswith(("/", "@")):
            raise ValueError("Choices must be short narrative actions, not commands")
        if any(unicodedata.category(char).startswith("C") for char in text):
            raise ValueError("Choice contains control characters")
        choices.append(text)
    if len({unicodedata.normalize("NFKC", text).casefold() for text in choices}) != len(choices):
        raise ValueError("Choices must be distinct")
    return choices


def _unfence(source: str) -> str:
    if len(source) > MAX_RESPONSE_CHARS:
        raise ValueError("Light Novel output exceeds the bounded protocol size")
    text = source.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    return fenced.group(1).strip() if fenced else text


def _choice_validation_result(value: object, requested_count: int) -> tuple[list[str] | None, str | None, int | None]:
    observed_count = len(value) if isinstance(value, list) else None
    try:
        return validate_choices(value, requested_count), None, observed_count
    except ValueError as exc:
        message = str(exc)
        if value is None:
            reason = "missing_inline_choices"
        elif not isinstance(value, list):
            reason = "invalid_choice_container"
        elif "Expected exactly" in message:
            reason = "invalid_choice_count"
        elif "must be text" in message:
            reason = "non_text_choice"
        elif "short narrative actions" in message:
            reason = "invalid_choice_action"
        elif "control characters" in message:
            reason = "control_character"
        elif "distinct" in message:
            reason = "duplicate_choices"
        else:
            reason = "invalid_choice_payload"
        return None, reason, observed_count


def _parse_story_envelope_diagnostic(
    value: object, requested_count: int
) -> tuple[str, list[str] | None, str | None, int | None] | None:
    if not isinstance(value, dict):
        return None
    story = value.get("story")
    if not isinstance(story, str) or not story.strip():
        raise ValueError("Story response has no usable narrative")
    choices, reason, observed_count = _choice_validation_result(value.get("choices"), requested_count)
    return story.strip(), choices, reason, observed_count


def _trailing_fenced_envelope(text: str) -> tuple[str, object | None] | None:
    fenced = re.search(r"(?:^|\n)```json\s*([\s\S]*?)\s*```\s*$", text, re.IGNORECASE)
    if fenced is None:
        return None
    prefix = text[: fenced.start()].strip()
    if not prefix:
        return None
    try:
        value = json.loads(fenced.group(1).strip())
    except (ValueError, TypeError):
        value = None
    return prefix, value


def _trailing_unfenced_envelope(text: str) -> tuple[str, object] | None:
    candidates = tuple(re.finditer(r"(?m)^[ \t]*(\{)", text))
    for candidate in reversed(candidates):
        prefix = text[: candidate.start()].strip()
        if not prefix:
            continue
        try:
            value = json.loads(text[candidate.start(1) :].strip())
        except (ValueError, TypeError):
            continue
        return prefix, value
    return None


def parse_story_response_diagnostic(
    source: str, requested_count: int
) -> tuple[str, list[str] | None, str | None, int | None]:
    text = _unfence(source)
    try:
        result = json.loads(text)
    except (ValueError, TypeError):
        result = None
    parsed = _parse_story_envelope_diagnostic(result, requested_count)
    if parsed is not None:
        return parsed
    trailing = _trailing_fenced_envelope(text)
    if trailing is not None:
        prefix, trailing_value = trailing
        fence_count = text.count("```")
        if fence_count == 2 and isinstance(trailing_value, dict):
            trailing_story = trailing_value.get("story")
            if isinstance(trailing_story, str) and trailing_story.strip():
                parsed_trailing = _parse_story_envelope_diagnostic(trailing_value, requested_count)
                if parsed_trailing is not None:
                    return parsed_trailing
            if set(trailing_value) == {"choices"}:
                choices, reason, observed_count = _choice_validation_result(
                    trailing_value.get("choices"), requested_count
                )
                return prefix, choices, reason, observed_count
        reason = "ambiguous_inline_envelope" if fence_count != 2 else "invalid_inline_envelope"
        if trailing_value is None:
            reason = "malformed_inline_json"
        trailing_choices = trailing_value.get("choices") if isinstance(trailing_value, dict) else None
        observed_count = len(trailing_choices) if isinstance(trailing_choices, list) else None
        return prefix, None, reason, observed_count
    trailing = _trailing_unfenced_envelope(text)
    if trailing is not None:
        prefix, trailing_value = trailing
        if isinstance(trailing_value, dict):
            trailing_story = trailing_value.get("story")
            if isinstance(trailing_story, str) and trailing_story.strip():
                parsed_trailing = _parse_story_envelope_diagnostic(trailing_value, requested_count)
                if parsed_trailing is not None:
                    story, choices, reason, observed_count = parsed_trailing
                    return (prefix if prefix.endswith(story) else story), choices, reason, observed_count
            if set(trailing_value) == {"choices"}:
                choices, reason, observed_count = _choice_validation_result(
                    trailing_value.get("choices"), requested_count
                )
                return prefix, choices, reason, observed_count
        trailing_choices = trailing_value.get("choices") if isinstance(trailing_value, dict) else None
        observed_count = len(trailing_choices) if isinstance(trailing_choices, list) else None
        return prefix, None, "invalid_inline_envelope", observed_count
    # A malformed choice tail must not discard an already complete JSON story string.
    if text.startswith("{"):
        match = re.match(r'\{\s*"story"\s*:\s*', text)
        if match:
            try:
                story, _end = json.JSONDecoder().raw_decode(text[match.end() :])
                if isinstance(story, str) and story.strip():
                    return story.strip(), None, "malformed_inline_json", None
            except ValueError:
                pass
        raise ValueError("Malformed story envelope; no complete narrative to recover")
    if not text or text.startswith(("[", "```")):
        raise ValueError("Story response has no usable narrative")
    # Some providers ignore the envelope instruction entirely; preserve ordinary prose only.
    return text, None, "missing_inline_choices", None


def parse_story_response(source: str, requested_count: int) -> tuple[str, list[str] | None]:
    story, choices, _reason, _observed_count = parse_story_response_diagnostic(source, requested_count)
    return story, choices


def parse_choice_response(source: str, requested_count: int) -> list[str]:
    value = json.loads(_unfence(source))
    return validate_choices(value.get("choices") if isinstance(value, dict) else value, requested_count)


def inline_instruction(count: int, language: str) -> str:
    return (
        "Light Novel response contract: return a JSON object with exactly two keys: "
        '"story" (the complete narrative as a JSON string) and "choices" (an array of '
        f"exactly {count} distinct next actions for the USER, each 1–{MAX_CHOICE_CHARS} characters). "
        "Do not choose for the user, predict outcomes, put menu text inside the story, or include slash commands. "
        "Preserve the character, persona, world and all established story context. "
        f"Both narrative and actions must match response language {language or 'auto (the conversation language)'}. "
        "No markdown fences, explanations or extra keys."
    )


def add_inline_contract(messages: list[dict], count: int, language: str) -> list[dict]:
    result = [dict(message) for message in messages]
    instruction = inline_instruction(count, language)
    if result and result[0].get("role") == "system":
        result[0]["content"] = str(result[0].get("content") or "") + "\n\n" + instruction
    else:
        result.insert(0, {"role": "system", "content": instruction})
    return result
