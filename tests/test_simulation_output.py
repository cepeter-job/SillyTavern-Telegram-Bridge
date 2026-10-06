"""Reserved tracker protocol never reaches presentation or new story history."""

from copy import deepcopy

import pytest
from application_test_setup import make_test_persona_service
from settings_test_support import make_test_settings
from test_npc_generation_wiring import _fields, _session

from bridge.generation import build_chat_messages
from bridge.telegram_output import telegram_safe_output, telegram_transport_output


@pytest.mark.parametrize(
    "hidden",
    [
        "<internal_states>SECRET</internal_states>",
        '<INTERNAL_STATES mode="private">SECRET</INTERNAL_STATES>',
        "<internal_states>SECRET<internal_states>NESTED</internal_states>SECRET</internal_states>",
        "&lt;internal_states&gt;SECRET&lt;/internal_states&gt;",
        "&amp;lt;internal_states&amp;gt;SECRET&amp;lt;/internal_states&amp;gt;",
        "&#60;internal_states&#62;SECRET&#60;/internal_states&#62;",
        "<internal_states>A&amp;lt;internal_states&amp;gt;B</internal_states>C&amp;lt;/internal_states&amp;gt;",
    ],
)
def test_transport_drops_reserved_blocks_before_flattening_html(hidden):
    assert telegram_transport_output("Before." + hidden + "After.") == "Before.After."
    assert telegram_safe_output("Before." + hidden + "After.") == "Before.After."


@pytest.mark.parametrize("tail", ["<internal_states>SECRET", "<internal_states private", "<internal_st"])
def test_incomplete_reserved_tail_never_reaches_final_transport(tail):
    assert telegram_transport_output("Before." + tail) == "Before."


@pytest.mark.parametrize("end", range(1, len("<internal_states>") + 1))
def test_streaming_withholds_every_partial_reserved_opener(end):
    assert telegram_safe_output("Before." + "<internal_states>"[:end]) == "Before."


@pytest.mark.parametrize("opener", ["&lt;internal_st", "&amp;lt;internal_st", "&#60;internal_st"])
def test_streaming_withholds_encoded_partial_reserved_openers(opener):
    assert telegram_safe_output("Before." + opener) == "Before."


def test_ordinary_spoilers_and_similar_names_are_preserved():
    assert telegram_transport_output("<tg-spoiler>Surprise</tg-spoiler>") == "<tg-spoiler>Surprise</tg-spoiler>"
    assert telegram_safe_output("<internal_statesman>Visible</internal_statesman>") == "Visible"


def test_effective_prompt_removes_only_recognized_templates_without_mutating_inputs(tmp_path):
    session, fields = _session(), _fields()
    session["system_prompt"] = (
        "Keep the historical setting.\n## Internal States\n"
        "Output every turn: BOND, Sparks, Grudge.\n## Dialogue\nUse distinct voices."
    )
    fields["post_history_instructions"] = "Preserve agency. <internal_states>BOND Sparks Grudge</internal_states>"
    original = deepcopy((session, fields))
    messages = build_chat_messages(
        session,
        fields,
        "Continue",
        [("assistant", "Story.<internal_states>SECRET")],
        persona_service=make_test_persona_service(),
        app_settings=make_test_settings(home=tmp_path),
        defer_compaction=True,
    )
    system = messages[0]["content"]
    assert "Keep the historical setting." in system and "Use distinct voices." in system
    assert "Preserve agency." in system and "Output every turn:" not in system
    assert "SECRET" not in str(messages) and messages[1]["content"] == "Story."
    assert (session, fields) == original


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


def test_nested_legacy_sections_preserve_the_following_custom_instructions():
    from bridge.simulation_output import strip_legacy_tracker_templates

    source = (
        "Before.\n## Internal States\nOutput BOND Sparks Grudge.\n"
        "### Internal States\nOutput BOND Sparks Grudge.\n"
        "## Dialogue\nKeep these important instructions.\n"
    )
    assert strip_legacy_tracker_templates(source) == "Before.\n## Dialogue\nKeep these important instructions.\n"


def test_reserved_angle_decoder_preserves_unrelated_entities_and_handles_chained_escapes():
    from bridge.simulation_output import strip_internal_state_blocks

    ordinary = "&lt;tg-spoiler&gt;Visible &amp; literal&lt;/tg-spoiler&gt;"
    edge = "&" + "amp;" * 32
    hidden = edge + "lt;internal_states" + edge + "gt;SECRET" + edge + "lt;/internal_states" + edge + "gt;"
    assert strip_internal_state_blocks(ordinary + hidden + "End.") == ordinary + "End."


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
    assert "Never emit Internal States" in messages[0]["content"]
    compacted, _ = compact_chat_messages(messages, budget_tokens=900, app_settings=make_test_settings(home=tmp_path))
    assert "Keep latest input" in str(compacted) and len(str(compacted)) < len(str(messages))
    assert "Never emit Internal States" in compacted[0]["content"]
