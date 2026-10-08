"""Fail-closed structural continuity proof; no assertion of semantic equivalence."""

import pytest

from bridge.context_continuity_proof import verify_continuity


def valid_manifest():
    kinds = ("fact", "causal", "promise", "negation", "reader_knowledge", "branch_boundary")
    witnesses = []
    source_rows = []
    for index, kind in enumerate(kinds):
        row_id = index + 11
        digest = f"{index + 1:064x}"
        source_rows.append({"row_id": row_id, "digest": digest, "branch": "A", "revision": 2})
        witnesses.append(
            {
                "fact_id": f"f{row_id}",
                "kind": kind,
                "source_row_id": row_id,
                "source_digest": digest,
                "branch": "A",
                "revision": 2,
                "visibility": "restricted" if kind == "reader_knowledge" else "shared",
                "known_by": ["rowan"] if kind == "reader_knowledge" else [],
                "depends_on": ["f11"] if kind in {"causal", "promise", "negation"} else [],
                "retained": True,
            }
        )
    return {
        "schema_version": 1,
        "authority": "synthetic_only",
        "scope": {
            "session_created_at": 1.0,
            "revision": 2,
            "branch": "A",
            "reader": "rowan",
            "through_rowid": 20,
            "accepted_covered_id": 16,
            "invalidated_from_id": None,
        },
        "removed_row_ids": list(range(11, 17)),
        "source_rows": source_rows,
        "required_fact_ids": [w["fact_id"] for w in witnesses],
        "witnesses": witnesses,
    }


def test_complete_structural_evidence_can_pass_without_authorizing_pruning():
    report = verify_continuity(valid_manifest())
    assert report["structural_ok"] is True
    assert report["reason_codes"] == []
    assert report["activation_allowed"] is False
    assert report["native_causal_proof"] is False
    assert report["provider_requests"] == 0
    assert report["checked_required"] == 6


@pytest.mark.parametrize(
    "mutation,reason",
    [
        (lambda m: m["scope"].update(invalidated_from_id=12), "pending_invalidation"),
        (lambda m: m["scope"].update(accepted_covered_id=14), "incomplete_coverage"),
        (lambda m: m["scope"].update(branch="B"), "branch_changed"),
        (lambda m: m["scope"].update(revision=3), "revision_changed"),
        (lambda m: m["scope"].update(reader="mira"), "reader_not_authorized"),
        (lambda m: m["source_rows"][0].update(digest="e" * 64), "source_changed"),
        (lambda m: m["witnesses"][1].update(depends_on=["missing"]), "causal_support_missing"),
        (lambda m: m["witnesses"][2].update(retained=False), "required_witness_missing"),
        (lambda m: m["witnesses"][3].update(source_digest="e" * 64), "source_changed"),
        (lambda m: m["removed_row_ids"].append(17), "source_row_missing"),
        (lambda m: m["witnesses"].pop(), "required_witness_missing"),
        (lambda m: m["scope"].update(session_created_at=0), "invalid_scope"),
        (lambda m: m.update(authority="native_proven"), "untrusted_authority"),
        (lambda m: m["witnesses"][1].update(visibility="restricted", known_by=["mira"]), "reader_not_authorized"),
    ],
)
def test_any_missing_causal_support_or_scope_mismatch_fails_closed(mutation, reason):
    manifest = valid_manifest()
    mutation(manifest)
    report = verify_continuity(manifest)
    assert report["structural_ok"] is False
    assert reason in report["reason_codes"]
    assert report["activation_allowed"] is False


def test_private_canaries_are_never_exported_or_accepted():
    manifest = valid_manifest()
    manifest["source_rows"][0]["PRIVATE_STORY_CANARY"] = "secret prompt"
    result = verify_continuity(manifest)
    assert result["structural_ok"] is False
    assert "secret prompt" not in str(result)
    assert result["activation_allowed"] is False


def test_repeated_and_negated_sources_require_distinct_canonical_witnesses():
    manifest = valid_manifest()
    manifest["witnesses"][3]["source_row_id"] = 12
    result = verify_continuity(manifest)
    assert result["structural_ok"] is False
    assert "source_changed" in result["reason_codes"] or "source_row_missing" in result["reason_codes"]


def test_untrusted_semantics_cannot_be_satisfied_by_a_model_claim():
    manifest = valid_manifest()
    manifest["semantic_equivalent"] = True
    manifest["human_review_passed"] = True
    report = verify_continuity(manifest)
    assert report["activation_allowed"] is False
    assert report["structural_ok"] is False


def test_causal_dependency_cycle_is_not_a_valid_closure():
    manifest = valid_manifest()
    manifest["witnesses"][0]["depends_on"] = ["f12"]
    manifest["witnesses"][1]["depends_on"] = ["f11"]
    report = verify_continuity(manifest)
    assert report["structural_ok"] is False
    assert "causal_cycle" in report["reason_codes"]


def test_required_dependency_must_be_retained_for_every_parent_edge():
    manifest = valid_manifest()
    manifest["witnesses"][0]["retained"] = False
    report = verify_continuity(manifest)
    assert not report["structural_ok"]
    assert "required_witness_missing" in report["reason_codes"]
    assert "causal_support_missing" in report["reason_codes"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda m: m["scope"].update(session_created_at=float("nan")),
        lambda m: m["scope"].update(session_created_at=float("inf")),
        lambda m: m["witnesses"][0].update(kind=[]),
        lambda m: m.update(schema_version=True),
        lambda m: m["scope"].update(through_rowid=0),
    ],
)
def test_malformed_or_nonfinite_proofs_fail_without_exception(mutation):
    manifest = valid_manifest()
    mutation(manifest)
    report = verify_continuity(manifest)
    assert report["structural_ok"] is False
    assert report["activation_allowed"] is False
