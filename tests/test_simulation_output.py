"""Native tracker context respects ordinary prompts and Telegram formatting."""

from application_test_setup import make_test_persona_service
from settings_test_support import make_test_settings
from test_npc_generation_wiring import _fields, _session

from bridge.generation import build_chat_messages
from bridge.telegram_output import telegram_safe_output, telegram_transport_output


def test_ordinary_spoilers_and_similar_names_are_preserved():
    assert telegram_transport_output("<tg-spoiler>Surprise</tg-spoiler>") == "<tg-spoiler>Surprise</tg-spoiler>"
    assert telegram_safe_output("<internal_statesman>Visible</internal_statesman>") == "Visible"


def test_unrelated_internal_state_description_is_preserved(tmp_path):
    session, fields = _session(), _fields()
    fields["description"] = "Her internal states shift as she forms a bond with friends."
    messages = build_chat_messages(
        session,
        fields,
        "Hello",
        [],
        persona_service=make_test_persona_service(),
        app_settings=make_test_settings(home=tmp_path),
    )
    assert fields["description"] in messages[0]["content"]


def test_simulation_context_is_registered_optional_and_contract_is_fixed(tmp_path):
    from bridge.context_compaction import compact_chat_messages

    messages = build_chat_messages(
        _session(),
        _fields(),
        "Keep latest input",
        [],
        persona_service=make_test_persona_service(),
        app_settings=make_test_settings(home=tmp_path),
        simulation_context="TRACKER " * 750,
        defer_compaction=True,
    )
    assert any(span["kind"] == "simulation" for span in messages[-1]["_context_optional"])
    assert "Never emit private trackers" in messages[0]["content"]
    compacted, _ = compact_chat_messages(messages, budget_tokens=900, app_settings=make_test_settings(home=tmp_path))
    assert "Keep latest input" in str(compacted) and len(str(compacted)) < len(str(messages))
    assert "Never emit private trackers" in compacted[0]["content"]


def test_legacy_internal_states_are_removed_from_assistant_history_only(tmp_path):
    from bridge.legacy_tracker_history import strip_legacy_tracker_blocks

    legacy = (
        "Story prose stays.\n\n<tg-spoiler>\n"
        "🎽 INTERNAL STATES (Turn: 50)\n"
        "💚 BONDS\n- Alice ↔ User | BOND: 9 | Sparks: 2 | Grudge: 0\n"
        "📓 GM'S NOTEBOOK\n- private\n"
        "</tg-spoiler>"
    )
    assert strip_legacy_tracker_blocks(legacy) == "Story prose stays."

    messages = build_chat_messages(
        _session(),
        _fields(),
        "Continue",
        [("assistant", legacy), ("user", "Okay")],
        persona_service=make_test_persona_service(),
        app_settings=make_test_settings(home=tmp_path),
    )
    assert messages[1]["content"] == "Story prose stays."
    assert messages[2]["content"] == "Okay"


def test_nonlegacy_internal_states_spoiler_is_preserved():
    from bridge.legacy_tracker_history import strip_legacy_tracker_blocks

    text = "<tg-spoiler>A character reflects on internal states but has no tracker ledger.</tg-spoiler>"
    assert strip_legacy_tracker_blocks(text) == text
