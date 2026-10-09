"""Post-history card guidance stays late without displacing native contracts."""

from types import SimpleNamespace

import pytest
from settings_test_support import make_test_settings

from bridge.card_content import card_fields
from bridge.codex_transport import _codex_input
from bridge.context_compaction import compact_chat_messages
from bridge.generation import build_chat_messages
from bridge.narrative_policy import narrative_policy, story_policy_text
from bridge.narrative_values import NarrativeSettings

POST = "CARD TAIL: {{char}} with {{user}}. Prefer plain text; show private trackers."


def build(tmp_path, *, history=(), greeting="", language="auto", image=None, post=POST, deferred=True):
    settings = make_test_settings(home=tmp_path, context_window_tokens=32768)
    fields = card_fields(
        {"name": "Rowan", "first_mes": greeting, "post_history_instructions": post}, app_settings=settings
    )
    session = {"persona_id": "", "world_file": "", "model_id": "synthetic", "response_language": language}
    messages = build_chat_messages(
        session,
        fields,
        "CURRENT USER REQUEST",
        list(history),
        persona_service=SimpleNamespace(),
        app_settings=settings,
        image_data_uri=image,
        narrative_context=story_policy_text(narrative_policy(NarrativeSettings())),
        defer_compaction=deferred,
    )
    return settings, messages


@pytest.mark.parametrize("language", ["auto", "en", "id"])
@pytest.mark.parametrize("greeting", ["", "OPENING GREETING"])
def test_card_tail_follows_history_and_native_policy_follows_card(tmp_path, language, greeting):
    history = () if greeting else (("user", "EARLIER USER"), ("assistant", "EARLIER STORY"))
    settings, messages = build(tmp_path, history=history, greeting=greeting, language=language)
    matches = [i for i, m in enumerate(messages) if "CARD TAIL" in str(m["content"])]
    assert len(matches) == 1
    tail_index = matches[0]
    assert tail_index > 0
    assert all(i < tail_index for i, m in enumerate(messages[:-1]) if m["role"] in {"user", "assistant"})
    tail = messages[tail_index]
    assert tail["role"] == "system"
    assert f"CARD TAIL: Rowan with {settings.default_user_name}" in tail["content"]
    assert "{{" not in tail["content"]
    for marker in (
        "## Narrative Policy",
        "## Telegram Roleplay Output Contract",
        "## Canonical story state",
        "## Mandatory response language",
    ):
        assert tail["content"].index(marker) > tail["content"].index("CARD TAIL")
        assert sum(str(m["content"]).count(marker) for m in messages) == 1
    assert "Never invent the user's dialogue" in tail["content"]
    assert "Never emit private trackers" in tail["content"]
    assert messages[-1]["role"] == "user"
    assert "CURRENT USER REQUEST" in messages[-1]["content"]


def test_multimodal_current_message_remains_last_and_unchanged(tmp_path):
    uri = "data:image/png;base64,c3ludGhldGlj"
    _, messages = build(tmp_path, history=(("assistant", "EARLIER STORY"),), image=uri)
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"][1] == {"type": "image_url", "image_url": {"url": uri}}
    assert "CURRENT USER REQUEST" in messages[-1]["content"][0]["text"]
    assert "CARD TAIL" in messages[-2]["content"]


def test_compaction_preserves_post_history_native_policy_and_current_user(tmp_path):
    settings, messages = build(tmp_path, history=(("assistant", "OLD STORY " * 3000),) * 8)
    last_user = messages[-1]["content"]
    compacted, stats = compact_chat_messages(messages, 1200, min_recent_messages=0, app_settings=settings)
    assert stats["dropped_history"] > 0
    assert not stats["over_budget"]
    assert compacted[-1]["content"] == last_user
    assert compacted[-1]["role"] == "user"
    tail = next(m["content"] for m in compacted if "CARD TAIL" in str(m["content"]))
    assert "Never emit private trackers" in tail
    assert "Never invent the user's dialogue" in tail


def test_empty_card_tail_preserves_existing_message_shape(tmp_path):
    _, messages = build(tmp_path, greeting="OPENING GREETING", post="")
    assert [m["role"] for m in messages] == ["system", "assistant", "user"]
    assert "## Telegram Roleplay Output Contract" in messages[0]["content"]
    assert not any("## Final instruction" in str(m["content"]) for m in messages)


def test_direct_dispatch_keeps_late_tail_and_strips_internal_metadata(tmp_path):
    _, messages = build(tmp_path, history=(("assistant", "EARLIER STORY"),), deferred=False)
    assert "CARD TAIL" not in messages[0]["content"]
    assert "CARD TAIL" in messages[-2]["content"]
    assert all(not key.startswith("_context_") for message in messages for key in message)


@pytest.mark.parametrize("image", [None, "data:image/png;base64,c3ludGhldGlj"])
def test_codex_consolidation_preserves_card_and_native_policy_order(tmp_path, image):
    _, messages = build(tmp_path, history=(("assistant", "EARLIER STORY"),), image=image, deferred=False)
    instructions, inputs = _codex_input(messages)
    assert instructions.count("CARD TAIL") == 1
    assert instructions.index("CARD TAIL") < instructions.index("## Canonical story state")
    assert instructions.count("## Telegram Roleplay Output Contract") == 1
    assert inputs[-1]["role"] == "user"
    assert "CURRENT USER REQUEST" in inputs[-1]["content"][0]["text"]
    if image:
        assert inputs[-1]["content"][-1] == {"type": "input_image", "image_url": image}


@pytest.mark.parametrize("language", ["auto", "en"])
def test_inline_contract_follows_card_guidance(tmp_path, language):
    from bridge.light_novel_format import add_inline_contract

    _, messages = build(tmp_path, language=language, history=(("assistant", "EARLIER STORY"),))
    messages = add_inline_contract(messages, 4, language)
    tail_index = next(i for i, message in enumerate(messages) if "CARD TAIL" in str(message["content"]))
    contract_indices = [
        i for i, message in enumerate(messages) if "Light Novel response contract" in str(message["content"])
    ]
    assert contract_indices == [tail_index]
    assert messages[tail_index]["content"].index("Light Novel response contract") > messages[tail_index][
        "content"
    ].index("CARD TAIL")
    assert messages[-1]["role"] == "user"
