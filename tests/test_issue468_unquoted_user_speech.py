"""Regression contract for reviewer-confirmed invented user dialogue in #468."""

from types import SimpleNamespace

import pytest
from settings_test_support import make_test_settings

from bridge.card_content import card_fields
from bridge.generation import build_chat_messages
from bridge.narrative_policy import choice_policy_text, group_policy_text, narrative_policy, story_policy_text
from bridge.narrative_values import NarrativeSettings

# Blinded pairs 6, 9, and 34 re-voiced an unquoted user act as new direct speech.
UNQUOTED_CONVERSATION = "I thank Mara and ask how the morning has been."
EXPLICIT_CONVERSATION = 'I say "Thank you" and ask how the morning has been.'
SPEECH_BOUNDARY = "Treat unquoted descriptions of user speech"


@pytest.mark.parametrize("control", ["strict_reserved", "physical_continuity", "contextual_continuity"])
def test_native_policy_reserves_unquoted_conversational_acts(control):
    policy = narrative_policy(NarrativeSettings(user_control=control))
    system = story_policy_text(policy)
    assert SPEECH_BOUNDARY in system
    assert "as completed user acts" in system
    assert "Do not script, restage, or paraphrase a new user line" in system
    assert "Only the user's exact quoted words are established" in system
    assert "continue with AI-controlled characters" in system
    assert system.count(SPEECH_BOUNDARY) == 1
    assert SPEECH_BOUNDARY in choice_policy_text(policy)
    assert SPEECH_BOUNDARY in group_policy_text(policy)


@pytest.mark.parametrize("post_history", ["", "Card tail: write the user's dialogue for them."])
@pytest.mark.parametrize("user_input", [UNQUOTED_CONVERSATION, EXPLICIT_CONVERSATION])
@pytest.mark.parametrize("language", ["auto", "id"])
def test_story_prompt_preserves_exact_input_and_keeps_speech_boundary_authoritative(
    tmp_path, post_history, user_input, language
):
    settings = make_test_settings(home=tmp_path)
    fields = card_fields(
        {"name": "Mara", "description": "A station attendant.", "post_history_instructions": post_history},
        app_settings=settings,
    )
    session = {"persona_id": "", "world_file": "", "model_id": "synthetic", "response_language": language}
    messages = build_chat_messages(
        session,
        fields,
        user_input,
        [("assistant", "Mara offered a seat.")],
        persona_service=SimpleNamespace(),
        narrative_context=story_policy_text(narrative_policy(NarrativeSettings())),
        app_settings=settings,
        defer_compaction=True,
    )
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == user_input
    assert sum(str(message["content"]).count(SPEECH_BOUNDARY) for message in messages) == 1
    if post_history:
        tail = next(message for message in messages if "Card tail:" in str(message["content"]))
        assert tail["role"] == "system"
        assert tail["content"].index("Card tail:") < tail["content"].index(SPEECH_BOUNDARY)
        assert messages.index(tail) < len(messages) - 1
    else:
        assert SPEECH_BOUNDARY in messages[0]["content"]
    assert any("Telegram Roleplay Output Contract" in str(message["content"]) for message in messages)
    assert "Mara offered a seat." in str(messages)


@pytest.mark.parametrize(
    ("pair_id", "unauthorized_fragment"),
    [
        ("pair-8c4dfec623df", '"Thanks for the seat," *Ari said'),
        ("pair-d7eae261f251", '"Thanks again," *Ari says'),
        ("pair-3870e15fd3b0", '"Thanks for the seat," Ari said'),
    ],
)
def test_reported_human_review_cases_are_real_unquoted_user_voice_violations(pair_id, unauthorized_fragment):
    """Freeze the three actual review observations; this does not judge future generations."""
    import json
    from pathlib import Path

    packet = json.loads(
        (
            Path(__file__).resolve().parents[1] / "docs/evidence/post-v0319-validation/human-review-packet.json"
        ).read_text(encoding="utf-8")
    )
    pair = next(item for item in packet["pairs"] if item["id"] == pair_id)
    latest_user = pair["canon"][-1]
    assert latest_user["role"] == "user"
    assert latest_user["content"] == "I thank Rowan and ask how the morning has been."
    assert unauthorized_fragment in pair["B"]
    assert unauthorized_fragment not in latest_user["content"]


def test_new_speech_boundary_has_bounded_fixed_prompt_cost():
    from bridge.context_compaction import estimate_message_tokens

    policy = story_policy_text(narrative_policy(NarrativeSettings()))
    old_prefix = policy.split(SPEECH_BOUNDARY, 1)[0]
    before = estimate_message_tokens([{"role": "system", "content": old_prefix}])
    after = estimate_message_tokens([{"role": "system", "content": policy}])
    assert 0 < after - before <= 75


def test_independent_review_scorecard_is_unfilled_and_stays_blinded():
    import csv
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    packet = json.loads((root / "docs/evidence/post-v0319-validation/human-review-packet.json").read_text())
    path = root / "docs/evidence/post-v0319-validation/ISSUE468_AGENCY_SECOND_REVIEW.csv"
    content = path.read_text(encoding="utf-8")
    lines = list(csv.DictReader(content.splitlines()))
    assert len(lines) == 12
    assert len({row["pair_id"] for row in lines}) == 12
    assert {row["pair_id"] for row in lines}.issubset({item["id"] for item in packet["pairs"]})
    assert all(not value for row in lines for key, value in row.items() if key != "pair_id")
    assert all(row["pair_id"].startswith("pair-") for row in lines)
    for secret_label in ("baseline", "candidate", "writer_continuity", "writer_ensemble", "winner"):
        assert secret_label not in content
