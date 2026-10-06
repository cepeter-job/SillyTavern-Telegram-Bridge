"""Regression contracts for canonical pure helpers, not transport facades."""

from __future__ import annotations

import importlib
import importlib.util

import pytest


def shared(name):
    assert importlib.util.find_spec(name) is not None, f"Missing canonical helper owner: {name}"
    return importlib.import_module(name)


@pytest.mark.parametrize("value, expected", [("", 0), ("ASCII", 5), ("日本語", 3), ("🙂", 2), ("A🙂中", 4)])
def test_utf16_counts_code_units(value, expected):
    assert shared("bridge.text_units").utf16_length(value) == expected


@pytest.mark.parametrize("count", range(6))
def test_escape_parity_uses_only_immediately_preceding_backslashes(count):
    text = "x" + "\\" * count + '"'
    helper = shared("bridge.text_units").is_escaped_at
    assert helper(text, len(text) - 1) is bool(count % 2)
    assert helper(text, 0) is False


@pytest.mark.parametrize(
    "value, expected",
    [
        (' {"ok":1} ', '{"ok":1}'),
        ('```json\n{"ok":1}\n```', '{"ok":1}'),
        ("```JSON\n[]\n```", "[]"),
        ("```\n[]\n```", "[]"),
        ("```json\n[]", "```json\n[]"),
        (None, ""),
    ],
)
def test_unfence_preserves_existing_extractor_contract(value, expected):
    assert shared("bridge.json_fences").unfence_json(value) == expected


@pytest.mark.parametrize(
    "values, expected",
    [
        (["  Alice  Smith ", "alice smith", "BOB", "bob", "", None], ("Alice Smith", "BOB")),
        (("Straße", "STRASSE"), ("Straße",)),
        ("Alice", ()),
        (None, ()),
        ({"Alice": 1}, ()),
    ],
)
def test_actor_visibility_keeps_display_spelling_and_casefold_dedup(values, expected):
    assert shared("bridge.actor_visibility").normalize_known_by(values) == expected


def test_npc_sql_decode_remains_raw_while_episodic_decode_normalizes():
    from bridge.episodic_memory import _decode_known_by as episodic
    from bridge.npc_repository import _decode_known_by as npc

    value = '[" A ","a",""]'
    assert npc(value) == (" A ", "a", "")
    assert episodic(value) == ("A",)


@pytest.mark.parametrize(
    "parts, expected", [([], []), (["text"], ["text", "\n"]), (["text "], ["text "]), (["text\n"], ["text\n"])]
)
def test_html_newline_helper_preserves_whitespace_contract(parts, expected):
    from bridge import telegram_output

    assert hasattr(telegram_output, "_append_newline"), "Missing shared HTML newline helper"
    telegram_output._append_newline(parts)
    assert parts == expected
