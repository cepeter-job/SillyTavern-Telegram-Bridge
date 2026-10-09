"""Native System Prompt catalogues must not partially import full presets."""

import json
from types import SimpleNamespace

import pytest

from bridge import card_content, system_prompt_panels


@pytest.fixture
def settings(tmp_path, app_settings_builder):
    app_settings_builder.system_prompts_dir = tmp_path
    return app_settings_builder.build()


@pytest.mark.parametrize(
    "structure",
    [
        {"prompts": [{"identifier": "main", "content": "PRIVATE MAIN"}]},
        {"prompt_order": [{"character_id": 100001, "order": []}]},
        {"prompts": [], "prompt_order": []},
        {"prompts": None},
        {"prompt_order": "malformed"},
        {"prompts": [], "prompt": "PRIVATE FLATTENED"},
    ],
)
def test_full_export_is_not_a_native_catalogue(tmp_path, settings, caplog, structure):
    raw = dict(structure, name="Export", impersonation_prompt="PRIVATE IMPERSONATION")
    path = tmp_path / "export.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    (tmp_path / "valid.txt").write_text("Native story guidance", encoding="utf-8")

    result = card_content.load_system_prompts(app_settings=settings)

    assert result == {"valid": {"name": "Valid", "prompt": "Native story guidance"}}
    assert "Chat Completion preset" in caplog.text
    assert "PRIVATE" not in caplog.text
    assert path.read_text(encoding="utf-8") == json.dumps(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Plain JSON text", {"native": {"name": "Native", "prompt": "Plain JSON text"}}),
        (["First", "Second"], {"native": {"name": "Native", "prompt": "First\nSecond"}}),
        (
            {"id": "voice", "name": "Voice", "content": "Speak naturally", "post_history": "Ignored"},
            {"voice": {"name": "Voice", "prompt": "Speak naturally"}},
        ),
        (
            {"plain": "Plain voice", "nested": {"name": "Named voice", "prompt": "Other voice"}},
            {
                "plain": {"name": "plain", "prompt": "Plain voice"},
                "nested": {"name": "Named voice", "prompt": "Other voice"},
            },
        ),
    ],
)
def test_native_json_formats_are_preserved(tmp_path, settings, raw, expected):
    (tmp_path / "native.json").write_text(json.dumps(raw), encoding="utf-8")
    assert card_content.load_system_prompts(app_settings=settings) == expected


@pytest.mark.parametrize("suffix", ["json", "txt"])
def test_invalid_utf8_does_not_hide_valid_files(tmp_path, settings, suffix, caplog):
    (tmp_path / f"broken.{suffix}").write_bytes(b"\xff")
    (tmp_path / "valid.txt").write_text("Valid prompt", encoding="utf-8")
    assert set(card_content.load_system_prompts(app_settings=settings)) == {"valid"}
    assert "Could not read System Prompt" in caplog.text


def test_malformed_json_does_not_hide_valid_files(tmp_path, settings):
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    (tmp_path / "valid.txt").write_text("Valid prompt", encoding="utf-8")
    assert set(card_content.load_system_prompts(app_settings=settings)) == {"valid"}


def test_prompt_menu_explains_supported_format_without_printing_bodies(tmp_path, settings, monkeypatch):
    (tmp_path / "native.txt").write_text("PRIVATE BODY", encoding="utf-8")
    sent = []
    monkeypatch.setattr(system_prompt_panels, "send_panel_message", lambda *a, **k: sent.append(a))
    system_prompt_panels.send_system_prompt_menu(
        "test-token", "chat", "", request_context=SimpleNamespace(app_settings=settings)
    )
    text = sent[0][2]
    assert "TXT or native JSON" in text
    assert "Chat Completion presets are not supported" in text
    assert "PRIVATE BODY" not in text


def test_native_text_keeps_unsupported_macros_visible_for_manual_adaptation(tmp_path, settings):
    text = "Use {{char}}. {{setvar::voice::quiet}}{{getvar::voice}}"
    (tmp_path / "native.txt").write_text(text, encoding="utf-8")
    assert card_content.get_system_prompt_choice("native", app_settings=settings) == text
