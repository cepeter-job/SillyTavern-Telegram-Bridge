"""Frozen synthetic evidence stays reproducible; one checksum is not a credential."""

import hashlib
import json
from pathlib import Path

import pytest

from tools.native_context_trial import summarize_trial
from tools.run_native_context_trial import protocol_digest, validate_trial_state

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/evidence/issue421-native-continuity-v1"
FINDING = (
    "9c6ed11f7a42f511dfc26936f816d739b62558ad:"
    "docs/evidence/issue421-native-continuity-v1/evidence-manifest.json:generic-api-key:10"
)


def test_all_frozen_public_evidence_digests_match():
    manifest = json.loads((EVIDENCE / "evidence-manifest.json").read_text())
    for filename, expected in manifest["artifacts"].items():
        assert Path(filename).name == filename
        assert hashlib.sha256((EVIDENCE / filename).read_bytes()).hexdigest() == expected
    assert manifest["model_requests"] == 24
    assert manifest["human_approved"] is False
    assert manifest["production_activation_allowed"] is False


def test_scanner_exception_is_only_the_verified_public_label_map_checksum():
    manifest = json.loads((EVIDENCE / "evidence-manifest.json").read_text())
    labels = json.loads((EVIDENCE / "unmasking_key.json").read_text())
    assert all(set(pair.values()) == {"baseline", "candidate"} for pair in labels["key"].values())
    expected = hashlib.sha256((EVIDENCE / "unmasking_key.json").read_bytes()).hexdigest()
    assert manifest["artifacts"]["unmasking_key.json"] == expected
    ignored = (ROOT / ".gitleaksignore").read_text().splitlines()
    assert ignored.count(FINDING) == 1
    assert [line for line in ignored if "issue421-native-continuity-v1" in line] == [FINDING]


def test_locked_original_trial_recomputes_without_network():
    plan = json.loads((EVIDENCE / "frozen-plan.json").read_text())
    state = json.loads((EVIDENCE / "state.json").read_text())
    report = json.loads((EVIDENCE / "report.json").read_text())
    # The immutable v1 plan describes the historical implementation. Lifecycle
    # maintenance changes the current source digest, so it must not authorize
    # current execution. Replaying recorded results does not dispatch requests.
    assert plan["implementation_sha256"] != protocol_digest()
    with pytest.raises(ValueError, match=r"^native_trial_protocol_changed$"):
        validate_trial_state(plan, state)
    recomputed = summarize_trial(plan, state)
    # Python versions may round summation by one ULP. Only this aggregate
    # estimate may differ; frozen artifact hashes and all other evidence
    # fields must still match exactly, including every quality/rollout gate.
    frozen_weighted = report["measured"]["case_weighted_reduction_fraction"]
    actual_weighted = recomputed["measured"]["case_weighted_reduction_fraction"]
    assert abs(actual_weighted - frozen_weighted) <= 1e-15
    recomputed["measured"]["case_weighted_reduction_fraction"] = frozen_weighted
    assert report == recomputed
    assert report["matched_target_met"] is True
    assert report["automated_review_passed"] is False
    assert report["human_approved"] is False
    assert report["production_activation_allowed"] is False
