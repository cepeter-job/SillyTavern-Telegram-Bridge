"""Hybrid previews require current native evidence, not caller-provided proof flags."""

import json
from dataclasses import replace

import pytest

from bridge.context_hybrid_sources import capture_hybrid_sources
from bridge.memory_scope_store import resolve_memory_scope
from tools.hybrid_context_fixture import MARKER, native_fixture


@pytest.fixture
def story(tmp_path):
    data = native_fixture(tmp_path)
    yield data
    data[0].close()


def test_native_source_snapshot_filters_private_blocks_and_does_not_write(story):
    db, scope, messages, rows = story
    before = db.total_changes
    captured = capture_hybrid_sources(db, scope, messages)
    assert len(captured.rows) == len(rows)
    assert len(captured.fingerprint) == 64
    assert captured.windows and all(w.through_rowid <= scope.through_rowid for w in captured.windows)
    assert "PRIVATE_HYBRID_CANARY" not in repr(captured.windows)
    assert db.total_changes == before
    mira = resolve_memory_scope(db, "c", {"session_id": "s"}, {"name": "Mira"})
    private = capture_hybrid_sources(db, mira, messages)
    assert any("PRIVATE_HYBRID_CANARY" in block.text for w in private.windows for block in w.blocks)
    assert private.fingerprint != captured.fingerprint


@pytest.mark.parametrize("change", ["gap", "checkpoint", "classification", "pending", "purge", "rewritten"])
def test_missing_or_revoked_native_evidence_rejects_snapshot(story, change):
    db, scope, messages, rows = story
    if change == "gap":
        db.execute("DELETE FROM memory_segments WHERE layer='summary' AND start_id=?", (rows[8][0],))
    elif change == "checkpoint":
        db.execute("DELETE FROM memory_layer_checkpoints WHERE layer='summary'")
    elif change == "classification":
        db.execute("DELETE FROM memory_artifact_visibility WHERE artifact_kind='summary'")
    elif change == "pending":
        db.execute("UPDATE memory_layer_state SET invalidated_from_id=? WHERE layer='summary'", (rows[6][0],))
    elif change == "purge":
        db.execute("UPDATE memory_layer_state SET purge_epoch=purge_epoch+1 WHERE layer='summary'")
    else:
        db.execute("UPDATE messages SET content='Changed agreement.' WHERE id=?", (rows[4][0],))
    db.commit()
    with pytest.raises(ValueError):
        capture_hybrid_sources(db, scope, messages)


@pytest.mark.parametrize("change", ["history", "reader", "foreign", "incarnation", "marker", "order"])
def test_unverifiable_source_or_reader_never_authorizes_compression(story, change):
    db, scope, messages, _rows = story
    if change == "history":
        scope = replace(scope, historical=True)
    elif change == "reader":
        scope = replace(scope, principals=())
    elif change == "foreign":
        scope = replace(scope, session_id="other")
    elif change == "incarnation":
        scope = replace(scope, session_created_at=99.0)
    elif change == "marker":
        messages[2][MARKER] = True
    else:
        messages[3], messages[4] = messages[4], messages[3]
    with pytest.raises(ValueError):
        capture_hybrid_sources(db, scope, messages)


def test_forged_archive_digest_is_rejected_not_silently_ignored(story):
    db, scope, messages, _rows = story
    row = db.execute(
        "SELECT through_id,source_document_id FROM memory_layer_checkpoints "
        "WHERE layer='summary' ORDER BY through_id LIMIT 1"
    ).fetchone()
    db.execute(
        "INSERT INTO summary_archive_windows VALUES(?,?,?,?,?,?,?)",
        (
            scope.chat_id,
            scope.session_id,
            scope.session_created_at,
            row[0],
            row[1],
            "0" * 64,
            json.dumps([{"text": "Forged prior state", "visibility": "shared", "known_by": []}]),
        ),
    )
    db.commit()
    with pytest.raises(ValueError):
        capture_hybrid_sources(db, scope, messages)


def test_modified_sidecar_classification_must_match_accepted_checkpoint(story):
    db, scope, messages, _ = story
    payload = [{"text": "Forged newly public knowledge", "visibility": "shared", "known_by": []}]
    db.execute(
        "UPDATE memory_artifact_visibility SET blocks_json=? WHERE artifact_kind='summary'", (json.dumps(payload),)
    )
    db.commit()
    with pytest.raises(ValueError):
        capture_hybrid_sources(db, scope, messages)


def test_valid_native_archive_is_authorized_and_rechecked(story):
    from bridge.memory_artifact_store import parse_classified_blocks
    from bridge.memory_fact_store import digest_value

    db, scope, messages, _ = story
    through, document, payload = db.execute(
        "SELECT through_id,source_document_id,payload_json FROM memory_layer_checkpoints "
        "WHERE layer='summary' ORDER BY through_id LIMIT 1"
    ).fetchone()
    blocks = parse_classified_blocks(json.loads(payload))
    db.execute(
        "INSERT INTO summary_archive_windows VALUES(?,?,?,?,?,?,?)",
        (
            scope.chat_id,
            scope.session_id,
            scope.session_created_at,
            through,
            document,
            digest_value(blocks),
            json.dumps(blocks),
        ),
    )
    db.commit()
    before = capture_hybrid_sources(db, scope, messages)
    assert len(before.windows) == 2
    assert all("PRIVATE_HYBRID_CANARY" not in b.text for w in before.windows for b in w.blocks)
    db.execute(
        "UPDATE summary_archive_windows SET blocks_json=?",
        (json.dumps([{"text": "Different forged fact", "visibility": "shared", "known_by": []}]),),
    )
    db.commit()
    with pytest.raises(ValueError):
        capture_hybrid_sources(db, scope, messages)


def test_archived_source_survives_independent_checkpoint_retention_cleanup(story):
    from bridge.memory_artifact_store import parse_classified_blocks
    from bridge.memory_fact_store import digest_value

    db, scope, messages, _ = story
    through, document, payload = db.execute(
        "SELECT through_id,source_document_id,payload_json FROM memory_layer_checkpoints "
        "WHERE layer='summary' ORDER BY through_id LIMIT 1"
    ).fetchone()
    blocks = parse_classified_blocks(json.loads(payload))
    db.execute(
        "INSERT INTO summary_archive_windows VALUES(?,?,?,?,?,?,?)",
        (
            scope.chat_id,
            scope.session_id,
            scope.session_created_at,
            through,
            document,
            digest_value(blocks),
            json.dumps(blocks),
        ),
    )
    db.execute("DELETE FROM memory_layer_checkpoints WHERE layer='summary' AND through_id=?", (through,))
    db.commit()
    result = capture_hybrid_sources(db, scope, messages)
    assert len(result.windows) == 2
    assert result.windows[0].through_rowid == through
    assert result.windows[0].source_document == document
