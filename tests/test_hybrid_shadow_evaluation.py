"""Offline measurements distinguish source checks, estimates and rollout approval."""

import json

import pytest

from tools.evaluate_hybrid_context import build_report


def test_fixed_workload_keeps_negative_controls_and_never_reports_measured_savings(tmp_path):
    report = build_report(tmp_path)
    assert report["provider_requests"] == 0
    assert report["production_activation_allowed"] is False
    assert report["provider_measured_reduction"] is None
    assert report["semantic_equivalence_proven"] is False
    cases = {case["case_id"]: case for case in report["cases"]}
    assert set(cases) == {
        "unique_dialogue",
        "commitment_dense",
        "indonesian",
        "unsupported_script",
        "short_history",
        "large_character_card",
    }
    assert cases["unique_dialogue"]["candidate_status"] == "preview"
    assert cases["unique_dialogue"]["estimated_reduction_fraction"] > 0.3
    for name in ("commitment_dense", "unsupported_script", "short_history"):
        assert cases[name]["estimated_reduction_fraction"] == 0
        assert cases[name]["candidate_status"] == "fallback"
    assert (
        cases["large_character_card"]["estimated_reduction_fraction"]
        < cases["unique_dialogue"]["estimated_reduction_fraction"]
    )
    assert all(case["dispatch_uses_full_history"] for case in report["cases"])
    assert "PRIVATE_HYBRID_CANARY" not in json.dumps(report)
    assert sum(case["weight"] for case in report["cases"]) == pytest.approx(1.0)


def test_evidence_cli_never_overwrites_a_previous_checkpoint(tmp_path, monkeypatch):
    from tools.evaluate_hybrid_context import main

    saved = tmp_path / "existing.json"
    saved.write_text("previous immutable evidence")
    monkeypatch.setattr("sys.argv", ["evaluate_hybrid_context.py", "--output", str(saved)])
    with pytest.raises(SystemExit) as rejected:
        main()
    assert rejected.value.code == 2
    assert saved.read_text() == "previous immutable evidence"


def test_live_cli_requires_explicit_metadata_only(tmp_path, monkeypatch):
    from tools.evaluate_hybrid_context import main

    output = tmp_path / "report.json"
    monkeypatch.setattr(
        "sys.argv",
        ["evaluate_hybrid_context.py", "--database", str(tmp_path / "database.sqlite3"), "--output", str(output)],
    )
    with pytest.raises(SystemExit) as rejected:
        main()
    assert rejected.value.code == 2
    assert not output.exists()


def test_live_metadata_without_an_authenticated_card_is_a_private_fallback(tmp_path):
    import sqlite3

    from bridge.settings import load_app_settings
    from tools.evaluate_hybrid_context import build_live_metadata
    from tools.hybrid_context_fixture import native_fixture

    database = tmp_path / "private-source.sqlite3"
    db, _scope, _messages, _rows = native_fixture(tmp_path, count=8, db=sqlite3.connect(database))
    db.close()
    before = database.read_bytes()
    report = build_live_metadata(database, load_app_settings({}, home=tmp_path))
    assert report["provider_requests"] == 0 and report["database_writes"] == 0
    assert report["production_transcript_exported"] is False
    assert report["cases"][0]["reason"] == "reader_card_unavailable"
    text = json.dumps(report)
    assert "PRIVATE_HYBRID_CANARY" not in text and "At display" not in text
    assert database.read_bytes() == before


def test_report_identity_binds_runtime_dispatch_and_context_contracts():
    from tools.evaluate_hybrid_context import CODE_FILES

    assert {
        "bridge/context_selection_runtime.py",
        "bridge/context_selection_store.py",
        "bridge/memory_contracts.py",
    }.issubset(CODE_FILES)
