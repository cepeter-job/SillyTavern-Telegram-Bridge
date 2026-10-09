#!/usr/bin/env python3
"""Validate blinded human scorecard *completeness*, never infer human quality approval.

The A/B assignment map and private provider observations are deliberately absent.
An attestation records a claim, not independent proof of a separate human reviewer.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.issue468_agency_trial import SHEET_COLUMNS  # noqa: E402

ORIGINAL_BLINDED_PACKET_SHA256 = "f461013fa5370c9afd53eaaccab0e5ac1f2b719f804364e54a6dfd2df3845228"

CATEGORIES = (
    "invented_user_speech",
    "unrequested_user_action",
    "knowledge_boundary",
    "causal_or_branch_error",
    "format_or_language_error",
)
DECISIONS = {"A", "B", "Tie", "Unresolved"}
RATINGS = {"Yes", "No", "Unclear"}
ATTESTATIONS = (
    "reviewed_by_human",
    "independent_of_implementation",
    "different_from_original_reviewer",
    "no_access_to_assignments_or_previous_scores",
    "reviewed_all_pairs",
)


class SubmissionError(ValueError):
    """The scorecard or reviewer attestation is not admissible."""


def _failure(reason: str, details: str = "") -> None:
    raise SubmissionError(f"{reason}: {details}")


def _is_nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def load_original_packet(path: Path) -> dict[str, Any]:
    """Reject altered or rewritten evidence before validating reviewer scores."""
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != ORIGINAL_BLINDED_PACKET_SHA256:
        _failure("wrong_blind_packet", "the source packet must match the originally frozen evidence")
    data = json.loads(raw)
    if not isinstance(data, dict):
        _failure("wrong_blind_packet", "expected source packet object")
    return data


def validate_submission(
    packet: dict[str, Any],
    rows: list[dict[str, Any]],
    attestation: dict[str, Any],
) -> dict[str, Any]:
    """Check all public review fields, keeping assignment labels concealed."""
    pairs = packet.get("pairs") if isinstance(packet, dict) else None
    if not isinstance(pairs, list) or len(pairs) != 16 or any(not isinstance(pair, dict) for pair in pairs):
        _failure("invalid_packet_inventory", "expected exactly 16 anonymous pairs")
    pair_ids = [pair.get("id") for pair in pairs]
    if (
        len(set(pair_ids)) != 16
        or any(not isinstance(key, str) or not key.startswith("pair-") for key in pair_ids)
        or any(not _is_nonempty(pair.get("A")) or not _is_nonempty(pair.get("B")) for pair in pairs)
    ):
        _failure("invalid_packet_inventory", "duplicate/malformed or blank A/B pair")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        _failure("scorecard_columns", "rows must be CSV dictionaries")
    schema = set(SHEET_COLUMNS)
    if any(set(row) != schema for row in rows):
        _failure("scorecard_columns", "scorecard header must match frozen blank CSV")
    row_ids = [row.get("pair_id") for row in rows]
    if Counter(row_ids) != Counter(pair_ids):
        _failure("pair_id_inventory", "each anonymous pair must have exactly one row")
    if not isinstance(attestation, dict):
        _failure("missing_reviewer_provenance", "reviewer declaration is missing")
    if any(not _is_nonempty(attestation.get(field)) for field in ("reviewer_id", "signed_name", "reviewed_at_utc")):
        _failure("missing_reviewer_provenance", "reviewer ID, signed name and UTC date required")
    if any(attestation.get(field) is not True for field in ATTESTATIONS):
        _failure("incomplete_reviewer_attestation", "all independent-review statements must be explicitly true")
    try:
        stamp = datetime.fromisoformat(attestation["reviewed_at_utc"].replace("Z", "+00:00"))
    except ValueError:
        _failure("missing_reviewer_provenance", "review date must be ISO-8601")
    if stamp.utcoffset() is None or stamp.utcoffset().total_seconds() != 0:
        _failure("missing_reviewer_provenance", "review date must carry UTC timezone")
    reviewer_id = attestation["reviewer_id"].strip()
    if any(row.get("independent_reviewer_id") != reviewer_id for row in rows):
        _failure("reviewer_mismatch", "the signed reviewer ID must match every pair")

    by_id = {pair["id"]: pair for pair in pairs}
    hard_responses = 0
    unresolved_items = 0
    flagged_categories = Counter()
    for row in rows:
        pair = by_id[row["pair_id"]]
        if row["preference_A_B_Tie_Unresolved"] not in DECISIONS or not _is_nonempty(row["reviewer_justification"]):
            _failure("incomplete_pair_adjudication", f"missing preference or reason: {row['pair_id']}")
        if row["preference_A_B_Tie_Unresolved"] == "Unresolved":
            unresolved_items += 1
        for side in ("A", "B"):
            yes_on_side = False
            for category in CATEGORIES:
                field = f"{side}_{category}"
                rating = row.get(field)
                if rating not in RATINGS:
                    _failure("invalid_review_value", f"{row['pair_id']}: {field} must be Yes/No/Unclear")
                if rating == "Unclear":
                    unresolved_items += 1
                if rating == "Yes":
                    yes_on_side = True
                    flagged_categories[category] += 1
            if yes_on_side:
                evidence = row.get(f"{side}_exact_error_quote", "")
                if not _is_nonempty(evidence):
                    _failure("missing_exact_error_quote", f"{row['pair_id']}: {side}")
                if evidence.strip() not in pair[side]:
                    _failure("quote_not_in_original_output", f"{row['pair_id']}: {side}")
                hard_responses += 1

    return {
        "pair_count": len(pairs),
        "reviewer_id": reviewer_id,
        "blinded_scorecard_valid": True,
        "recorded_hard_failures": hard_responses,
        "flagged_categories": dict(sorted(flagged_categories.items())),
        "unresolved_items": unresolved_items,
        "external_independence_verified": False,
        "human_quality_approved": False,
        "status": "human_submission_recorded_pending_independent_provenance_and_unblinding",
        "next_action": "independently verify reviewer provenance, then unblind privately and adjudicate",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--scorecard", type=Path, required=True)
    parser.add_argument("--declaration", type=Path, required=True)
    args = parser.parse_args()
    try:
        packet = load_original_packet(args.packet)
        with args.scorecard.open("r", encoding="utf-8-sig", newline="") as source:
            rows = list(csv.DictReader(source))
        declaration = json.loads(args.declaration.read_text(encoding="utf-8"))
        result = validate_submission(packet, rows, declaration)
    except (SubmissionError, ValueError, TypeError, KeyError, OSError) as exc:
        print(f"INCOMPLETE_BLINDED_HUMAN_REVIEW: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
