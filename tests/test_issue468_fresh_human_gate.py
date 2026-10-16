"""Frozen fresh-packet gate regression tests; all ratings below are synthetic test data."""

import csv
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools.issue468_human_gate import CATEGORIES, SubmissionError, load_original_packet, validate_submission

ROOT = Path(__file__).resolve().parents[1]
FRESH = ROOT / "docs/evidence/issue468-agency-followup/SECOND_INDEPENDENT_REVIEW/HUMAN_REVIEW.json"
BLANK = FRESH.with_name("HUMAN_SCORECARD.csv")


def fresh_inputs():
    packet = json.loads(FRESH.read_text(encoding="utf-8"))
    with BLANK.open(encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source))
    for row in rows:
        for side in ("A", "B"):
            for category in CATEGORIES:
                row[f"{side}_{category}"] = "No"
        row["preference_A_B_Tie_Unresolved"] = "Tie"
        row["reviewer_justification"] = "Synthetic structural fixture; not human quality evidence."
        row["independent_reviewer_id"] = "synthetic-reviewer"
    declaration = {
        "reviewer_id": "synthetic-reviewer",
        "signed_name": "Synthetic Test Fixture",
        "reviewed_at_utc": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
        "reviewed_by_human": True,
        "independent_of_implementation": True,
        "different_from_original_reviewer": True,
        "no_access_to_assignments_or_previous_scores": True,
        "reviewed_all_pairs": True,
    }
    return packet, rows, declaration


def test_exact_frozen_fresh_packet_loads_without_reorientation():
    assert hashlib.sha256(FRESH.read_bytes()).hexdigest() == (
        "4fcef6f4b715ab9ff53936e87d62a2d4f3fafc9649642d5e0db08dbdc0438088"
    )
    loaded = load_original_packet(FRESH)
    assert {pair["id"] for pair in loaded["pairs"]} == {row["pair_id"] for row in fresh_inputs()[1]}


def test_fresh_submission_records_two_categories_in_one_response_without_quality_approval():
    packet, rows, declaration = fresh_inputs()
    pair_id = "fresh-4a342b1e6f3d4bcb84"
    row = next(row for row in rows if row["pair_id"] == pair_id)
    row["A_invented_user_speech"] = "Yes"
    row["A_unrequested_user_action"] = "Yes"
    row["A_exact_error_quote"] = '"Thanks," *Ari says, settling into the chair across from Rowan.*'
    out = validate_submission(packet, rows, declaration)
    assert out["recorded_hard_failures"] == 1
    assert out["flagged_categories"] == {"invented_user_speech": 1, "unrequested_user_action": 1}
    assert out["unresolved_items"] == 0
    assert out["external_independence_verified"] is False
    assert out["human_quality_approved"] is False
    assert "baseline" not in json.dumps(out)
    assert "candidate" not in json.dumps(out)


@pytest.mark.parametrize("mutation", ["output", "id"])
def test_direct_fresh_validation_rejects_changed_frozen_evidence(mutation):
    packet, rows, declaration = fresh_inputs()
    if mutation == "output":
        packet["pairs"][0]["A"] += " An unreviewed continuation."
    else:
        changed_id = "fresh-000000000000000000"
        packet["pairs"][0]["id"] = changed_id
        rows[0]["pair_id"] = changed_id
    with pytest.raises(SubmissionError, match="wrong_blind_packet"):
        validate_submission(packet, rows, declaration)


def test_fresh_packet_loader_rejects_changed_bytes(tmp_path):
    changed = tmp_path / "changed.json"
    changed.write_bytes(FRESH.read_bytes() + b" ")
    with pytest.raises(SubmissionError, match="wrong_blind_packet"):
        load_original_packet(changed)


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("missing", "pair_id_inventory"),
        ("duplicate", "pair_id_inventory"),
        ("quote", "quote_not_in_original_output"),
    ],
)
def test_fresh_scorecard_keeps_strict_inventory_and_exact_quotes(mutation, reason):
    packet, rows, declaration = fresh_inputs()
    if mutation == "missing":
        rows.pop()
    elif mutation == "duplicate":
        rows[-1]["pair_id"] = rows[0]["pair_id"]
    else:
        row = next(row for row in rows if row["pair_id"] == "fresh-4a342b1e6f3d4bcb84")
        row["A_invented_user_speech"] = "Yes"
        row["A_exact_error_quote"] = '"Thanks," Ari says, settling into the chair across from Rowan.'
    with pytest.raises(SubmissionError, match=reason):
        validate_submission(packet, rows, declaration)
