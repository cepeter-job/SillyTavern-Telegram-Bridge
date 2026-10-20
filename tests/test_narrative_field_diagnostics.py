"""Field diagnostics identify contract failures without disclosing rejected content."""

import json

import pytest
from session_test_support import session_db as session_db
from test_narrative_acceptance_recovery import diagnostics, snapshot
from test_narrative_arcs import arc
from test_narrative_reconciliation import add_story, proposal, run_reconciliation

from bridge.diagnostic_events import clean_fields
from bridge.narrative_extraction import MAX_RECONCILIATION_OUTPUT, parse_narrative_extraction
from bridge.narrative_response import generate_narrative_response, narrative_failure_reason

PRIVATE = "PRIVATE_PAYLOAD_CANARY"


def candidate():
    data = json.loads(proposal())
    data["arcs"] = [arc(quote="A committed quotation.", rowid=1)]
    data["resolution"] = {"resolved": False, "evidence": []}
    return data


def replace_field(data, path, value):
    root = data
    parts = path.split(".")
    for part in parts[:-1]:
        data = data[int(part)] if isinstance(data, list) else data[part]
    key = int(parts[-1]) if isinstance(data, list) else parts[-1]
    data[key] = value
    return json.dumps(root)


@pytest.mark.parametrize(
    ("path", "value", "field", "code"),
    [
        ("schema_version", True, "schema_version", "invalid_type"),
        ("schema_version", 2, "schema_version", "unsupported_version"),
        ("story_phase", PRIVATE, "story_phase", "invalid_enum"),
        ("scene", [], "scene", "invalid_type"),
        ("scene.scene_id", "bad id", "scene.scene_id", "invalid_identifier"),
        ("scene.thread_id", None, "scene.thread_id", "invalid_type"),
        ("scene.viewpoint_character", PRIVATE * 20, "scene.viewpoint_character", "too_long"),
        ("scene.pov_mode", PRIVATE, "scene.pov_mode", "invalid_enum"),
        ("scene.user_present", 1, "scene.user_present", "invalid_type"),
        ("scene.purpose", [], "scene.purpose", "invalid_type"),
        ("scene.transition_type", PRIVATE, "scene.transition_type", "invalid_enum"),
        ("threads", {}, "threads", "invalid_type"),
        ("threads", [{}] * 9, "threads", "too_many_items"),
        ("threads.0", PRIVATE, "threads", "invalid_type"),
        ("threads.0.thread_id", "", "threads.thread_id", "empty"),
        ("threads.0.title", " ", "threads.title", "empty"),
        ("threads.0.status", PRIVATE, "threads.status", "invalid_enum"),
        ("threads.0.summary", PRIVATE * 100, "threads.summary", "too_long"),
        ("arcs", {}, "arcs", "invalid_type"),
        ("arcs", [{}] * 9, "arcs", "too_many_items"),
        ("arcs.0", PRIVATE, "arcs", "invalid_type"),
        ("arcs.0.arc_id", "bad id", "arcs.arc_id", "invalid_identifier"),
        ("arcs.0.title", " ", "arcs.title", "empty"),
        ("arcs.0.status", PRIVATE, "arcs.status", "invalid_enum"),
        ("arcs.0.phase", PRIVATE, "arcs.phase", "invalid_enum"),
        ("arcs.0.importance", PRIVATE, "arcs.importance", "invalid_enum"),
        ("arcs.0.summary", [], "arcs.summary", "invalid_type"),
        ("arcs.0.open_questions", {}, "arcs.open_questions", "invalid_type"),
        ("arcs.0.open_questions", [PRIVATE] * 9, "arcs.open_questions", "too_many_items"),
        ("arcs.0.open_questions", [PRIVATE] * 2, "arcs.open_questions", "duplicate"),
        ("arcs.0.related_threads", [None], "arcs.related_threads", "invalid_type"),
        ("arcs.0.related_threads", ["bad id"], "arcs.related_threads", "invalid_identifier"),
        ("arcs.0.evidence", {}, "arcs.evidence", "invalid_type"),
        ("arcs.0.evidence", [{}] * 5, "arcs.evidence", "too_many_items"),
        ("arcs.0.evidence.0", PRIVATE, "arcs.evidence", "invalid_type"),
        ("arcs.0.evidence.0.rowid", True, "arcs.evidence.rowid", "invalid_type"),
        ("arcs.0.evidence.0.rowid", 0, "arcs.evidence.rowid", "out_of_range"),
        ("arcs.0.evidence.0.quote", None, "arcs.evidence.quote", "invalid_type"),
        ("arcs.0.evidence.0.quote", " ", "arcs.evidence.quote", "empty"),
        ("arcs.0.evidence.0.quote", PRIVATE * 30, "arcs.evidence.quote", "too_long"),
        ("resolution", [], "resolution", "invalid_type"),
        ("resolution.resolved", 1, "resolution.resolved", "invalid_type"),
        ("resolution.evidence", [{"rowid": 0, "quote": PRIVATE}], "resolution.evidence.rowid", "out_of_range"),
    ],
    ids=lambda value: str(value)[:70],
)
def test_parser_identifies_the_first_failing_schema_field(path, value, field, code):
    with pytest.raises(ValueError) as caught:
        parse_narrative_extraction(replace_field(candidate(), path, value))
    assert getattr(caught.value, "validation_field", None) == field
    assert getattr(caught.value, "validation_code", None) == code
    assert PRIVATE not in str(caught.value)


@pytest.mark.parametrize(
    ("raw", "field", "code"),
    [
        ([], "output", "invalid_type"),
        ("[]", "output", "invalid_type"),
        (" " * (MAX_RECONCILIATION_OUTPUT + 1), "output", "too_long"),
    ],
    ids=["non_string", "non_object", "oversized"],
)
def test_output_envelope_diagnostics_are_content_free(raw, field, code):
    with pytest.raises(ValueError) as caught:
        parse_narrative_extraction(raw)
    assert getattr(caught.value, "validation_field", None) == field
    assert getattr(caught.value, "validation_code", None) == code


@pytest.mark.parametrize("collection", ["threads", "arcs", "arcs.0.evidence"])
def test_duplicates_identify_the_schema_field_not_the_entity(collection):
    data = candidate()
    items = data[collection] if collection in data else data["arcs"][0]["evidence"]
    raw = replace_field(data, collection, items * 2)
    with pytest.raises(ValueError) as caught:
        parse_narrative_extraction(raw)
    field = {"threads": "threads.thread_id", "arcs": "arcs.arc_id", "arcs.0.evidence": "arcs.evidence"}[collection]
    assert getattr(caught.value, "validation_field", None) == field
    assert getattr(caught.value, "validation_code", None) == "duplicate"


def test_encoded_collection_bound_keeps_its_existing_category():
    raw = replace_field(candidate(), "arcs.0.open_questions", ["\\" * 399 + str(i) for i in range(8)])
    with pytest.raises(ValueError) as caught:
        parse_narrative_extraction(raw)
    assert getattr(caught.value, "validation_field", None) == "arcs.open_questions"
    assert getattr(caught.value, "validation_code", None) == "storage_limit"
    assert narrative_failure_reason(caught.value, "parse") == "invalid_collection"


def generate(responses):
    calls = []

    def provider(*args, **kwargs):
        calls.append((args, kwargs))
        response = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(response, Exception):
            raise response
        return response

    result = generate_narrative_response(
        provider,
        "",
        "dummy::model",
        "PRIVATE_SOURCE_CANARY",
        session_id="session",
        settings={"max_tokens": 2400},
        source_valid=lambda: True,
        allow_epilogue=False,
        revision=7,
        through_rowid=10,
    )
    return result, calls


def test_retry_and_final_rejection_record_their_own_field_without_changing_retry(caplog):
    first = replace_field(candidate(), "scene.pov_mode", PRIVATE)
    second = replace_field(candidate(), "arcs.0.importance", PRIVATE)
    result, calls = generate([first, second])
    assert result is None and len(calls) == 2
    records = diagnostics(caplog)
    assert [(r["event"], r["validation_field"], r["validation_code"]) for r in records] == [
        ("narrative.extraction_retry", "scene.pov_mode", "invalid_enum"),
        ("narrative.reconciliation_rejected", "arcs.importance", "invalid_enum"),
    ]
    assert all(r["reason"] == "invalid_shape" for r in records)
    assert calls[1][0][2][1:] == calls[0][0][2]
    assert all(c[1]["settings"]["max_tokens"] == 2400 for c in calls)
    assert PRIVATE not in json.dumps(calls)
    assert "PRIVATE_" not in caplog.text + json.dumps(records)


def test_malformed_json_reports_only_the_output_field(caplog):
    result, calls = generate(['{"' + PRIVATE + '":'])
    assert result is None and len(calls) == 2
    assert all(
        r["validation_field"] == "output" and r["validation_code"] == "malformed_json" for r in diagnostics(caplog)
    )
    assert "PRIVATE_" not in caplog.text + json.dumps(diagnostics(caplog))


def test_provider_exceptions_cannot_masquerade_as_parser_diagnostics(caplog):
    with pytest.raises(ValueError) as caught:
        parse_narrative_extraction(replace_field(candidate(), "scene.pov_mode", PRIVATE))
    result, calls = generate([caught.value])
    assert result is None and len(calls) == 1
    record = diagnostics(caplog)[-1]
    assert record["reason"] == "provider_failed"
    assert "validation_field" not in record and "validation_code" not in record


def test_persisted_state_and_coverage_survive_field_specific_rejection(session_db, caplog):
    _, db, _ = session_db
    add_story(db, "PRIVATE_SOURCE_CANARY")
    before = snapshot(db)
    run_reconciliation(session_db, lambda *a, **k: replace_field(candidate(), "scene.pov_mode", PRIVATE))
    assert snapshot(db) == before
    assert diagnostics(caplog)[-1]["validation_field"] == "scene.pov_mode"
    assert "PRIVATE_" not in caplog.text + json.dumps(diagnostics(caplog))


def test_diagnostic_sanitizer_accepts_only_fixed_schema_fields_and_rules():
    safe = {"validation_field": "arcs.evidence.quote", "validation_code": "too_long"}
    assert clean_fields(safe) == safe
    assert clean_fields({"validation_field": PRIVATE, "validation_code": PRIVATE}) == {}
    assert clean_fields({"validation_field": "arcs." + PRIVATE, "validation_code": "too_long_" + PRIVATE}) == {}
    assert clean_fields({"validation_field": ["scene.pov_mode"], "validation_code": {"reason": "invalid_enum"}}) == {}
