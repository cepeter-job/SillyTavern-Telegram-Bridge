"""Malformed local persona metadata must fail closed without aborting a report."""

import hashlib
import json
import sqlite3

import pytest
from miniapp_test_support import card_bytes

import tools.extractive_live_replay as live
from bridge.settings import load_app_settings
from tools.hybrid_context_fixture import native_fixture


@pytest.mark.parametrize(
    ("metadata", "available"),
    [
        ({"personas": ["avatar.png"]}, False),
        ({"persona_descriptions": []}, False),
        ({"persona_descriptions": {"avatar.png": []}}, False),
        ({"persona_descriptions": {"avatar.png": None}}, False),
        ({"persona_descriptions": {"avatar.png": "PRIVATE_PERSONA_CANARY"}}, False),
        ({}, True),
        ({"persona_descriptions": {"avatar.png": {"description": "PRIVATE_PERSONA_CANARY"}}}, True),
    ],
    ids=[
        "list-catalog",
        "list-map",
        "list-entry",
        "null-entry",
        "text-entry",
        "missing-description",
        "valid-description",
    ],
)
def test_local_persona_shape_is_a_private_readonly_fallback(tmp_path, metadata, available):
    settings = load_app_settings({}, home=tmp_path)
    settings.character_dir.mkdir(parents=True, exist_ok=True)
    (settings.character_dir / "fixture.png").write_bytes(card_bytes("Rowan"))
    path = settings.native_persona_settings_file
    assert path.is_relative_to(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    power_user = {"personas": {"avatar.png": "Visitor"}, "persona_descriptions": {}}
    power_user.update(metadata)
    path.write_text(
        json.dumps({"power_user": power_user}),
        encoding="utf-8",
    )
    database = tmp_path / "snapshot.sqlite3"
    db = sqlite3.connect(":memory:")
    try:
        native_fixture(tmp_path, count=8, db=db)
        db.execute("UPDATE sessions SET character_file='fixture.png', persona_id='avatar.png' WHERE session_id='s'")
        db.commit()
        destination = sqlite3.connect(database)
        try:
            db.backup(destination)
        finally:
            destination.close()
    finally:
        db.close()
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    report = live.build_live_report(database, settings)
    assert report["estimated"]["all_local_core_denominators_available"] is available
    assert report["cases"][0]["reason"] == (
        "insufficient_older_history" if available else "local_core_reconstruction_unavailable"
    )
    if not available:
        assert report["estimated"]["local_core_aggregate_reduction_fraction"] is None
    assert report["production_database_writes"] == report["provider_requests"] == 0
    assert report["production_transcript_exported"] is False
    assert "PRIVATE_PERSONA_CANARY" not in json.dumps(report)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
