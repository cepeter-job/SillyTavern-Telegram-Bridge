"""Blinded review hides variant identity and never fabricates human approval."""

import json

import pytest

from tools.native_context_review import make_review_messages, parse_review, summarize_reviews

DIMENSIONS = ("voice", "causal", "agency", "knowledge", "grounding")


def judged(a=4, b=4, violations=None):
    return {
        "A": {"scores": dict.fromkeys(DIMENSIONS, a), "violation_codes": [], "rationale": "Grounded continuation."},
        "B": {
            "scores": dict.fromkeys(DIMENSIONS, b),
            "violation_codes": violations or [],
            "rationale": "Grounded continuation.",
        },
    }


def test_reviewer_receives_only_canon_task_and_anonymous_prose_not_representation_metadata():
    case = {
        "request": "Continue",
        "language": "en",
        "review_canon": ["No permission was given."],
        "case_id": "secret_label",
        "selection": {"reason": "lossless_dictionary"},
        "variants": {"baseline": {"prompt_sha256": "SECRET_HASH"}},
    }
    messages = make_review_messages(case, "Response left.", "Response right.")
    encoded = json.dumps(messages)
    assert "SECRET_HASH" not in encoded and "lossless_dictionary" not in encoded
    assert "baseline" not in encoded and "candidate" not in encoded
    payload = json.loads(messages[1]["content"])
    assert payload["responses"] == {"A": "Response left.", "B": "Response right."}
    assert payload["reference_canon"] == case["review_canon"]
    assert "secret_label" not in encoded


def test_order_swapped_scores_map_back_only_after_both_reviews_are_locked():
    first = parse_review(json.dumps(judged(a=4, b=5)))
    second = parse_review(json.dumps(judged(a=5, b=4)))
    result = summarize_reviews(
        [
            {"order": ["baseline", "candidate"], "judgment": first},
            {"order": ["candidate", "baseline"], "judgment": second},
        ]
    )
    assert result["automated_review_passed"] is True
    assert result["mean_scores"] == {"baseline": 4.0, "candidate": 5.0}
    assert result["human_approved"] is False
    assert result["production_activation_allowed"] is False


def test_opposite_preferences_after_swap_are_inconclusive_not_a_pass():
    result = summarize_reviews(
        [
            {"order": ["baseline", "candidate"], "judgment": parse_review(json.dumps(judged(a=5, b=4)))},
            {"order": ["candidate", "baseline"], "judgment": parse_review(json.dumps(judged(a=5, b=4)))},
        ]
    )
    assert result["automated_review_passed"] is False
    assert result["order_sensitive"] is True


def test_privacy_or_negation_error_fails_even_with_high_style_scores():
    data = parse_review(json.dumps(judged(a=5, b=5, violations=["knowledge_leak"])))
    result = summarize_reviews(
        [
            {"order": ["baseline", "candidate"], "judgment": data},
            {"order": ["candidate", "baseline"], "judgment": parse_review(json.dumps(judged()))},
        ]
    )
    assert not result["automated_review_passed"]
    assert "knowledge_leak" in result["candidate_violations"]


@pytest.mark.parametrize("text", ["{}", '{"A":true}', "```json\n{}\n```", '{"A":{},"A":{}}'])
def test_malformed_or_duplicate_review_cannot_grant_approval(text):
    with pytest.raises(ValueError):
        parse_review(text)


def test_unknown_scores_and_missing_second_review_are_not_accepted():
    data = judged()
    data["A"]["scores"]["voice"] = True
    with pytest.raises(ValueError):
        parse_review(json.dumps(data))
    with pytest.raises(ValueError):
        summarize_reviews([{"order": ["baseline", "candidate"], "judgment": judged()}])
