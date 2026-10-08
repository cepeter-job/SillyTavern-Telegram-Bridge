from pathlib import Path

import pytest

GUIDE = Path(__file__).resolve().parents[1] / "docs/user-guide.md"


@pytest.mark.parametrize(
    "expected_row",
    [
        "| Record a d20 result for an explicit action | `/check stealth 12 cross the courtyard` |",
        "| Explicit `/check <domain> <DC> <action>` | A local d20 roll and saved receipt, with no model request. |",
    ],
)
def test_manual_check_tables_keep_each_action_on_its_own_row(expected_row):
    lines = GUIDE.read_text(encoding="utf-8").splitlines()
    assert expected_row in lines
    table_rows = [line for line in lines if line.startswith("|")]
    assert all("\\n" not in row for row in table_rows)


def test_check_modes_are_changed_using_panel_buttons():
    text = GUIDE.read_text(encoding="utf-8")
    section = " ".join(text.split("### Natural action checks\n", 1)[1].split("\n### ", 1)[0].split())
    assert "panel buttons" in section
    assert all(f"**{mode}**" in section for mode in ("Auto", "Director", "Manual"))
    assert "do not change the mode" in section
    assert "only open this panel" not in section


@pytest.mark.parametrize("command", ["/check mode", "/check mode auto", "/check mode director", "/check mode manual"])
def test_raw_check_mode_commands_return_usage_without_database_access(command):
    import sqlite3
    from unittest.mock import Mock

    from application_test_setup import make_test_delivery_port

    from bridge.simulation_commands import handle_check_command

    sent = []
    delivery = make_test_delivery_port(send_text=lambda _token, _chat, text: sent.append(text))
    db = Mock(spec=sqlite3.Connection)
    # Invalid mode syntax must return before reading or mutating the database.
    handle_check_command(db, "token", "chat", "s1", command, "actor", None, delivery)
    assert db.method_calls == []
    assert sent == ["Use /check <domain> <DC> <action>. DC must be an integer from 1 to 20."]


def test_streaming_preview_is_reused_with_a_missing_preview_fallback():
    text = GUIDE.read_text(encoding="utf-8")
    section = " ".join(text.split("### Streaming and long replies\n", 1)[1].split("\n### ", 1)[0].split())
    assert "edits and reuses" in section
    assert "missing" in section and "new message" in section
    assert "removes it" not in section


def test_persona_selection_has_no_sole_persona_fallback():
    text = GUIDE.read_text(encoding="utf-8")
    section = " ".join(text.split("### Personas, Worlds and prompts\n", 1)[1].split("\n### ", 1)[0].split())
    assert "explicitly configured native default" in section
    assert "no Persona is selected" in section and "generic user name" in section
    assert "does not automatically select a sole available Persona" in section
