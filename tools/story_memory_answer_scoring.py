"""Exact synthetic fact contracts; lexical diagnostics are not semantic judgments."""

from __future__ import annotations


def normalized(text):
    return " ".join(text.casefold().split())


def score_answer(value, *, required, forbidden, eligible, facts):
    if not isinstance(value, dict) or set(value) != {"answer", "facts", "narrative"}:
        raise ValueError("answer_schema")
    if not all(isinstance(value[key], str) for key in ("answer", "narrative")):
        raise ValueError("answer_schema")
    if not isinstance(value["facts"], list) or len(value["facts"]) > len(facts):
        raise ValueError("answer_schema")
    claimed, correct, unknown, mismatches = set(), set(), [], []
    for claim in value["facts"]:
        if (
            not isinstance(claim, dict)
            or set(claim) != {"key", "statement"}
            or not all(isinstance(claim[key], str) for key in claim)
        ):
            raise ValueError("answer_schema")
        key = claim["key"]
        if key in claimed:
            raise ValueError("duplicate_claim")
        claimed.add(key)
        if key not in facts:
            unknown.append(key)
        elif normalized(claim["statement"]) != normalized(facts[key]):
            mismatches.append(key)
        else:
            correct.add(key)
    extraction = {
        "missing_required": [key for key in required if key not in correct],
        "forbidden_claims": [key for key in forbidden if key in claimed],
        "ineligible_claims": sorted(claimed.intersection(facts).difference(eligible)),
        "unknown_claims": unknown,
        "statement_mismatches": mismatches,
    }
    answer, narrative = normalized(value["answer"]), normalized(value["narrative"])
    eligible_text = {normalized(facts[key]) for key in eligible}
    prose = {
        "missing_required_answer": [key for key in required if normalized(facts[key]) not in answer],
        "missing_required_narrative": [key for key in required if normalized(facts[key]) not in narrative],
        "forbidden_mentions": [
            key
            for key in forbidden
            if normalized(facts[key]) not in eligible_text and normalized(facts[key]) in answer + "\n" + narrative
        ],
        "method": "casefold_whitespace_normalized_full_canonical_sentence_mentions",
    }
    violations = list(extraction.values()) + [
        prose[key] for key in ("missing_required_answer", "missing_required_narrative", "forbidden_mentions")
    ]
    return {
        "extraction": extraction,
        "prose": prose,
        "machine_checks_satisfied": not any(violations),
        "semantic_truth": "not_assessed",
    }
