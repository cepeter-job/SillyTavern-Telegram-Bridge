"""Report denominators and limitations are part of the safety contract."""

import importlib.util
import json


def report_builder():
    assert importlib.util.find_spec("tools.evaluate_extractive_context") is not None, "report not implemented"
    from tools.evaluate_extractive_context import build_report

    return build_report


def test_full_declared_prompts_keep_six_cases_and_no_savings_controls(tmp_path):
    report = report_builder()(tmp_path)
    cases = report["cases"]
    assert len(cases) == 6
    assert {c["case_id"] for c in cases} == {
        "unique_dialogue",
        "commitment_dense",
        "indonesian",
        "unsupported_script",
        "short_history",
        "large_character_card",
    }
    b = sum(c["baseline_tokens"] for c in cases)
    c = sum(c["candidate_tokens"] for c in cases)
    assert report["estimated"]["complete_prompt_baseline_tokens"] == b
    assert report["estimated"]["complete_prompt_candidate_tokens"] == c
    assert report["estimated"]["aggregate_reduction_fraction"] == 1 - c / b
    assert report["provider_measured_reduction"] is None
    assert report["provider_requests"] == 0
    assert report["production_activation_allowed"] is False
    assert report["independent_human_review_complete"] is False
    assert report["all_source_evidence_preserved"] is False
    for row in cases:
        if row["case_id"] in {"unsupported_script", "short_history"}:
            assert row["baseline_tokens"] == row["candidate_tokens"]


def test_report_digest_and_metrics_never_include_prompt_strings(tmp_path):
    report = report_builder()(tmp_path)
    assert len(report["implementation_sha256"]) == 64
    encoded = json.dumps(report)
    assert "PRIVATE_HYBRID_CANARY" not in encoded
    assert "I promise to preserve" not in encoded
    assert "session_id" not in encoded
    assert "Baseline source authentication does not prove omitted facts redundant." in report["limitations"]


def test_output_cannot_overwrite_existing_checkpoint(tmp_path):
    import pytest

    from tools.evaluate_extractive_context import write_report

    path = tmp_path / "report.json"
    write_report(path, {"saved": True})
    with pytest.raises(FileExistsError):
        write_report(path, {"saved": False})
    assert json.loads(path.read_text()) == {"saved": True}


def test_live_reconstruction_is_readonly_closed_and_never_full_prompt(tmp_path, monkeypatch):
    import sqlite3

    import pytest

    import tools.extractive_live_replay as live
    from bridge.settings import load_app_settings
    from tools.hybrid_context_fixture import native_fixture

    database = tmp_path / "live.sqlite3"
    db = sqlite3.connect(":memory:")
    native_fixture(tmp_path, count=8, db=db)
    destination = sqlite3.connect(database)
    try:
        db.backup(destination)
    finally:
        destination.close()
        db.close()
    original_connect = sqlite3.connect
    connections = []

    def observe_connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(live.sqlite3, "connect", observe_connect)
    report = live.build_live_report(database, load_app_settings({}, home=tmp_path))
    assert report["complete_live_prompt_captured"] is False
    assert report["production_database_writes"] == 0
    assert report["production_transcript_exported"] is False
    assert report["estimated"]["local_core_aggregate_reduction_fraction"] is None
    assert report["cases"][0]["reason"] == "local_core_reconstruction_unavailable"
    assert report["missing_prompt_components"]
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def test_live_local_core_reconstruction_keeps_private_sources_local(tmp_path):
    import hashlib
    import sqlite3

    from miniapp_test_support import card_bytes

    from bridge.settings import load_app_settings
    from tools.extractive_live_replay import build_live_report
    from tools.hybrid_context_fixture import native_fixture

    settings = load_app_settings({}, home=tmp_path)
    settings.character_dir.mkdir(parents=True, exist_ok=True)
    card = settings.character_dir / "fixture.png"
    card.write_bytes(card_bytes("Rowan"))
    db = sqlite3.connect(":memory:")
    native_fixture(tmp_path, count=8, db=db)
    db.execute("UPDATE sessions SET character_file='fixture.png' WHERE session_id='s'")
    db.commit()
    database = tmp_path / "sample.sqlite3"
    destination = sqlite3.connect(database)
    try:
        db.backup(destination)
    finally:
        destination.close()
        db.close()
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    report = build_live_report(database, settings)
    assert report["estimated"]["all_local_core_denominators_available"] is True
    assert report["cases"][0]["reason"] == "insufficient_older_history"
    assert report["cases"][0]["baseline_tokens"] > 0
    assert report["complete_live_prompt_captured"] is False
    assert report["production_database_writes"] == 0
    assert "PRIVATE_HYBRID_CANARY" not in json.dumps(report)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
