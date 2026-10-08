"""Offline structural continuity proof; semantic equivalence remains unproven.

This validator accepts *synthetic-only* opaque source and witness manifests.
Its result is never permission to prune live dialogue: native attestation,
causal semantics and blinded provider review require separate approvals.
"""

from __future__ import annotations

import math
import re

_REQUIRED_KINDS = frozenset(("fact", "causal", "promise", "negation", "reader_knowledge", "branch_boundary"))
_TOP_FIELDS = frozenset(
    ("schema_version", "authority", "scope", "removed_row_ids", "source_rows", "required_fact_ids", "witnesses")
)
_SCOPE_FIELDS = frozenset(
    (
        "session_created_at",
        "revision",
        "branch",
        "reader",
        "through_rowid",
        "accepted_covered_id",
        "invalidated_from_id",
    )
)
_SOURCE_FIELDS = frozenset(("row_id", "digest", "branch", "revision"))
_WITNESS_FIELDS = frozenset(
    (
        "fact_id",
        "kind",
        "source_row_id",
        "source_digest",
        "branch",
        "revision",
        "visibility",
        "known_by",
        "depends_on",
        "retained",
    )
)
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[a-zA-Z0-9_-]{1,64}\Z")


def _fixed_result(reasons: set[str], required: int) -> dict[str, object]:
    return {
        "schema_version": 1,
        "structural_ok": not reasons,
        "reason_codes": sorted(reasons),
        "checked_required": required,
        "native_causal_proof": False,
        "activation_allowed": False,
        "provider_requests": 0,
        "authority": "synthetic_only_structural_check",
    }


def _id(value: object) -> bool:
    return isinstance(value, str) and bool(_ID.fullmatch(value))


def _digest(value: object) -> bool:
    return isinstance(value, str) and bool(_DIGEST.fullmatch(value))


def _integer(value: object) -> bool:
    return type(value) is int and value >= 0


def verify_continuity(manifest: object) -> dict[str, object]:
    """Reject missing canonical witnesses, reader/branch leaks and unsupported claims.

    Output contains no source text, facts or reader identifiers.
    """
    reasons: set[str] = set()
    if (
        not isinstance(manifest, dict)
        or set(manifest) != _TOP_FIELDS
        or type(manifest.get("schema_version")) is not int
        or manifest["schema_version"] != 1
    ):
        return _fixed_result({"invalid_manifest"}, 0)
    if manifest.get("authority") != "synthetic_only":
        reasons.add("untrusted_authority")

    scope = manifest["scope"]
    if not isinstance(scope, dict) or set(scope) != _SCOPE_FIELDS:
        return _fixed_result(reasons | {"invalid_manifest"}, 0)
    created_at, revision, branch, reader = (
        scope["session_created_at"],
        scope["revision"],
        scope["branch"],
        scope["reader"],
    )
    through, covered, invalidation = (
        scope["through_rowid"],
        scope["accepted_covered_id"],
        scope["invalidated_from_id"],
    )
    if (
        type(created_at) not in (float, int)
        or not math.isfinite(created_at)
        or created_at <= 0
        or not _integer(revision)
        or not _id(branch)
        or not _id(reader)
        or not _integer(through)
        or through == 0
        or not _integer(covered)
        or (invalidation is not None and not _integer(invalidation))
    ):
        return _fixed_result(reasons | {"invalid_scope"}, 0)
    if invalidation is not None:
        reasons.add("pending_invalidation")

    removed = manifest["removed_row_ids"]
    if not isinstance(removed, list) or not removed or not all(_integer(x) and x > 0 for x in removed):
        return _fixed_result(reasons | {"invalid_manifest"}, 0)
    if removed != sorted(set(removed)):
        reasons.add("invalid_manifest")
    if max(removed) > covered or covered > through:
        reasons.add("incomplete_coverage")

    rows = manifest["source_rows"]
    if not isinstance(rows, list) or len(rows) > 256 or not rows:
        return _fixed_result(reasons | {"invalid_manifest"}, 0)
    indexed_rows: dict[int, dict] = {}
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != _SOURCE_FIELDS
            or not _integer(row["row_id"])
            or not _digest(row["digest"])
            or not _id(row["branch"])
            or not _integer(row["revision"])
        ):
            return _fixed_result(reasons | {"invalid_manifest"}, 0)
        if row["row_id"] in indexed_rows:
            reasons.add("invalid_manifest")
        indexed_rows[row["row_id"]] = row
        if row["branch"] != branch:
            reasons.add("branch_changed")
        if row["revision"] != revision:
            reasons.add("revision_changed")
    if not set(removed) <= set(indexed_rows):
        reasons.add("source_row_missing")

    witnesses = manifest["witnesses"]
    required = manifest["required_fact_ids"]
    if (
        not isinstance(witnesses, list)
        or len(witnesses) > 256
        or not isinstance(required, list)
        or not required
        or not all(_id(name) for name in required)
        or len(set(required)) != len(required)
    ):
        return _fixed_result(reasons | {"invalid_manifest"}, 0)
    indexed_witnesses: dict[str, dict] = {}
    for witness in witnesses:
        if not isinstance(witness, dict) or set(witness) != _WITNESS_FIELDS:
            return _fixed_result(reasons | {"invalid_manifest"}, 0)
        name = witness["fact_id"]
        if (
            not _id(name)
            or not isinstance(witness["kind"], str)
            or witness["kind"] not in _REQUIRED_KINDS
            or not _integer(witness["source_row_id"])
            or not _digest(witness["source_digest"])
            or not _id(witness["branch"])
            or not _integer(witness["revision"])
            or not isinstance(witness["visibility"], str)
            or witness["visibility"] not in {"shared", "restricted"}
            or not isinstance(witness["known_by"], list)
            or not all(_id(x) for x in witness["known_by"])
            or not isinstance(witness["depends_on"], list)
            or not all(_id(x) for x in witness["depends_on"])
            or type(witness["retained"]) is not bool
        ):
            return _fixed_result(reasons | {"invalid_manifest"}, 0)
        if name in indexed_witnesses:
            reasons.add("invalid_manifest")
        indexed_witnesses[name] = witness
        source = indexed_rows.get(witness["source_row_id"])
        if source is None:
            reasons.add("source_row_missing")
        elif source["digest"] != witness["source_digest"]:
            reasons.add("source_changed")
        if witness["branch"] != branch:
            reasons.add("branch_changed")
        if witness["revision"] != revision:
            reasons.add("revision_changed")
        if (witness["visibility"] == "restricted" and reader not in witness["known_by"]) or (
            witness["visibility"] == "shared" and witness["known_by"]
        ):
            reasons.add("reader_not_authorized")

    if not set(required) <= set(indexed_witnesses):
        reasons.add("required_witness_missing")
    retained = {name for name, witness in indexed_witnesses.items() if witness["retained"]}
    if not set(required) <= retained:
        reasons.add("required_witness_missing")
    if not {w["kind"] for w in indexed_witnesses.values() if w["fact_id"] in required} >= _REQUIRED_KINDS:
        reasons.add("required_witness_missing")
    if not set(removed) <= {w["source_row_id"] for w in indexed_witnesses.values() if w["retained"]}:
        reasons.add("source_row_missing")
    for witness in indexed_witnesses.values():
        if witness["fact_id"] in required or any(witness["fact_id"] in w["depends_on"] for w in witnesses):
            if witness["kind"] == "causal" and not witness["depends_on"]:
                reasons.add("causal_support_missing")
            if not set(witness["depends_on"]) <= retained:
                reasons.add("causal_support_missing")
    # Required causal ancestors form an acyclic graph. Two retained statements
    # referencing each other do not establish either event independently.
    active: set[str] = set()
    visited: set[str] = set()

    def visit(fact_id: str) -> bool:
        if fact_id in active:
            return False
        if fact_id in visited:
            return True
        witness = indexed_witnesses.get(fact_id)
        if witness is None:
            return True  # Missing dependencies already fail the retained check.
        active.add(fact_id)
        for ancestor in witness["depends_on"]:
            if not visit(ancestor):
                return False
        active.remove(fact_id)
        visited.add(fact_id)
        return True

    if any(not visit(fact_id) for fact_id in required):
        reasons.add("causal_cycle")
    return _fixed_result(reasons, len(required))
