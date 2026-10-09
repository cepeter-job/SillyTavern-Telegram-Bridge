"""Reviewer-submission structural gate. Does not judge the writing or certify human independence."""

import json
from pathlib import Path

import pytest

from tools.issue468_agency_trial import SHEET_COLUMNS
from tools.issue468_human_gate import SubmissionError, validate_submission

PACKET = Path(__file__).resolve().parents[1] / "docs/evidence/issue468-agency-followup/REVIEW_PACKET/HUMAN_REVIEW.json"


def valid_inputs():
    packet = json.loads(PACKET.read_text(encoding="utf-8"))
    rows = []
    for pair in packet["pairs"]:
        row = dict.fromkeys(SHEET_COLUMNS, "")
        row["pair_id"] = pair["id"]
        for side in ("A", "B"):
            for category in (
                "invented_user_speech",
                "unrequested_user_action",
                "knowledge_boundary",
                "causal_or_branch_error",
                "format_or_language_error",
            ):
                row[f"{side}_{category}"] = "No"
        row["preference_A_B_Tie_Unresolved"] = "Tie"
        row["reviewer_justification"] = "Both continuations preserve canon and user agency."
        row["independent_reviewer_id"] = "reviewer-two"
        rows.append(row)
    declaration = {
        "reviewer_id": "reviewer-two",
        "reviewed_by_human": True,
        "independent_of_implementation": True,
        "different_from_original_reviewer": True,
        "no_access_to_assignments_or_previous_scores": True,
        "reviewed_all_pairs": True,
        "signed_name": "Second Human Reviewer",
        "reviewed_at_utc": "2026-10-10T09:00:00Z",
    }
    return packet, rows, declaration


def evaluate(packet, rows, declaration):
    return validate_submission(packet, rows, declaration)


def expect_invalid(packet, rows, declaration, reason):
    with pytest.raises(SubmissionError, match=reason):
        evaluate(packet, rows, declaration)


def test_structurally_completed_review_does_not_automatically_approve_quality():
    packet, rows, attestation = valid_inputs()
    out = evaluate(packet, rows, attestation)
    assert out["pair_count"] == 16
    assert out["reviewer_id"] == "reviewer-two"
    assert out["recorded_hard_failures"] == 0
    assert out["unresolved_items"] == 0
    assert out["blinded_scorecard_valid"] is True
    assert out["external_independence_verified"] is False
    assert out["human_quality_approved"] is False
    assert "unblinding_map" not in out
    assert "pair_assignments" not in out
    assert "baseline" not in json.dumps(out)
    assert "candidate" not in json.dumps(out)


@pytest.mark.parametrize(
    "modification,reason",
    [
        ("missing", "pair_id_inventory"),
        ("duplicated", "pair_id_inventory"),
        ("unrecognized", "pair_id_inventory"),
        ("missing_column", "scorecard_columns"),
        ("extra_column", "scorecard_columns"),
    ],
)
def test_pair_ids_and_headers_must_match_original_packet_exactly(modification, reason):
    packet, rows, attestation = valid_inputs()
    if modification == "missing":
        rows.pop()
    elif modification == "duplicated":
        rows[-1]["pair_id"] = rows[0]["pair_id"]
    elif modification == "unrecognized":
        rows[0]["pair_id"] = "pair-000000000000"
    elif modification == "missing_column":
        rows[0].pop("A_format_or_language_error")
    elif modification == "extra_column":
        rows[0]["hidden_condition"] = "candidate"
    expect_invalid(packet, rows, attestation, reason)


@pytest.mark.parametrize(
    "cell",
    [
        "A_invented_user_speech",
        "B_unrequested_user_action",
        "A_knowledge_boundary",
        "B_causal_or_branch_error",
        "B_format_or_language_error",
    ],
)
@pytest.mark.parametrize("value", ["", "N/A", "5", "False"])
def test_missing_or_invalid_category_never_counts_as_clean(cell, value):
    packet, rows, attestation = valid_inputs()
    rows[0][cell] = value
    expect_invalid(packet, rows, attestation, "invalid_review_value")


def test_yes_requires_verbatim_quote_from_the_corresponding_output():
    packet, rows, attestation = valid_inputs()
    rows[0]["A_invented_user_speech"] = "Yes"
    expect_invalid(packet, rows, attestation, "missing_exact_error_quote")
    rows[0]["A_exact_error_quote"] = "this text never appeared in A"
    expect_invalid(packet, rows, attestation, "quote_not_in_original_output")
    rows[0]["A_exact_error_quote"] = packet["pairs"][0]["B"][:30]
    if rows[0]["A_exact_error_quote"] not in packet["pairs"][0]["A"]:
        expect_invalid(packet, rows, attestation, "quote_not_in_original_output")
    rows[0]["A_exact_error_quote"] = packet["pairs"][0]["A"][:50]
    outcome = evaluate(packet, rows, attestation)
    assert outcome["recorded_hard_failures"] == 1
    assert outcome["human_quality_approved"] is False


def test_yes_on_b_needs_b_quote_even_if_a_quote_is_present():
    packet, rows, attestation = valid_inputs()
    rows[0]["B_format_or_language_error"] = "Yes"
    rows[0]["A_exact_error_quote"] = packet["pairs"][0]["A"][:30]
    expect_invalid(packet, rows, attestation, "missing_exact_error_quote")


@pytest.mark.parametrize(
    "field",
    [
        "independent_of_implementation",
        "different_from_original_reviewer",
        "no_access_to_assignments_or_previous_scores",
        "reviewed_by_human",
        "reviewed_all_pairs",
    ],
)
def test_declaration_must_explicitly_assert_each_requirement(field):
    packet, rows, attestation = valid_inputs()
    attestation[field] = False
    expect_invalid(packet, rows, attestation, "incomplete_reviewer_attestation")
    attestation[field] = None
    expect_invalid(packet, rows, attestation, "incomplete_reviewer_attestation")


@pytest.mark.parametrize("field", ["signed_name", "reviewed_at_utc", "reviewer_id"])
def test_declaration_identifiers_are_required(field):
    packet, rows, attestation = valid_inputs()
    attestation[field] = ""
    expect_invalid(packet, rows, attestation, "missing_reviewer_provenance")


def test_reviewer_id_must_be_consistent_across_all_pairs():
    packet, rows, attestation = valid_inputs()
    rows[6]["independent_reviewer_id"] = "another"
    expect_invalid(packet, rows, attestation, "reviewer_mismatch")


@pytest.mark.parametrize(
    "field, value",
    [
        ("preference_A_B_Tie_Unresolved", ""),
        ("preference_A_B_Tie_Unresolved", "Winner"),
        ("reviewer_justification", ""),
    ],
)
def test_missing_preference_or_reason_prevents_adjudication(field, value):
    packet, rows, attestation = valid_inputs()
    rows[0][field] = value
    expect_invalid(packet, rows, attestation, "incomplete_pair_adjudication")


def test_unresolved_and_unclear_are_preserved_as_open_questions():
    packet, rows, attestation = valid_inputs()
    rows[2]["A_knowledge_boundary"] = "Unclear"
    rows[4]["preference_A_B_Tie_Unresolved"] = "Unresolved"
    out = evaluate(packet, rows, attestation)
    assert out["unresolved_items"] == 2
    assert out["human_quality_approved"] is False


def test_packet_never_accepts_duplicate_pair_ids():
    packet, rows, attestation = valid_inputs()
    packet["pairs"][-1]["id"] = packet["pairs"][0]["id"]
    expect_invalid(packet, rows, attestation, "invalid_packet_inventory")


def test_filled_review_is_never_an_independence_certificate():
    packet, rows, attestation = valid_inputs()
    attestation["signed_name"] = "Someone Claims Independence"
    out = evaluate(packet, rows, attestation)
    assert out["external_independence_verified"] is False
    assert out["human_quality_approved"] is False


def test_original_16_pair_packet_is_immutably_pinned(tmp_path):
    from tools.issue468_human_gate import load_original_packet

    original = load_original_packet(PACKET)
    assert len(original["pairs"]) == 16
    changed = json.loads(json.dumps(original))
    changed["pairs"][0]["A"] += " unreviewed addition"
    fake = tmp_path / "packet.json"
    fake.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(SubmissionError, match="wrong_blind_packet"):
        load_original_packet(fake)


def test_reviewer_facing_instructions_omit_unblinding_hints_and_previous_outcomes():
    root = PACKET.parent
    task = (root / "HUMAN_QUALITY_APPROVAL_TASK.md").read_text(encoding="utf-8")
    template = json.loads((root / "REVIEWER_DECLARATION_TEMPLATE.json").read_text())
    assert "16 pairs" in task
    assert "HUMAN_SCORECARD.csv" in task
    assert "REVIEWER_DECLARATION_TEMPLATE.json" in task
    assert "Yes" in task and "No" in task and "Unclear" in task
    for biased in ("baseline", "candidate", "pull/482", "three old", "+52", "0 updated"):
        assert biased not in task.casefold(), f"Reviewer task must not reveal: {biased}"
    for field in (
        "reviewed_by_human",
        "independent_of_implementation",
        "different_from_original_reviewer",
        "no_access_to_assignments_or_previous_scores",
        "reviewed_all_pairs",
    ):
        assert template[field] is None
    assert template["reviewer_id"] == ""
    assert template["signed_name"] == ""
    assert template["reviewed_at_utc"] == ""
