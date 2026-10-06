"""Alternate branches rebuild provenance from their own copied canonical rows."""

import json

from test_story_memory_artifacts import write_artifacts
from test_story_memory_scope import accept, append
from test_story_memory_scope import db as db
from test_story_memory_scope import forbid_real_hindsight_client as forbid_real_hindsight_client

from bridge.checkpoint_remap import remap_checkpoint
from bridge.episodic_memory import store_episodic_memory
from bridge.memory_fact_store import accept_source_facts, remember_local_fact
from bridge.memory_scope_store import read_episodic_block, resolve_memory_scope
from bridge.memory_store import next_source_segment
from bridge.sqlite_store import write_transaction


def clone_rows(db):
    mapping = {0: 0}
    with write_transaction(db):
        for row, role, content in db.execute(
            "SELECT id,role,content FROM messages WHERE chat_id='c' AND session_id='s' ORDER BY id"
        ).fetchall():
            copied = db.execute(
                "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('c','other',?,?,3)",
                (role, content),
            )
            mapping[row] = int(copied.lastrowid)
    return mapping


def test_branch_snapshot_remaps_attestations_explicit_anchors_and_classified_artifacts(db):
    from bridge.memory_artifact_store import read_summary_block
    from bridge.memory_snapshot_store import restore_local_memory_snapshot, snapshot_local_memory

    first = append(db)
    accept(db)
    remember_local_fact(db, "c", "s", "Mira", "The silver key is in my pocket.")
    last = append(db, "A later boundary.")
    write_artifacts(db, first)
    with write_transaction(db):
        store_episodic_memory(
            db,
            "c",
            "s",
            kind="fact",
            importance=0.9,
            summary="UNPROVEN LEGACY KEY",
            source_start_rowid=first,
            source_end_rowid=first,
        )
        db.execute("UPDATE memory_fact_index SET state='retained'")
    origin_documents = {row[0] for row in db.execute("SELECT document_id FROM memory_fact_index")}
    snapshot = snapshot_local_memory(db, "c", "s", last)
    assert len(snapshot["episodic"]) == 2
    assert all(document not in json.dumps(snapshot) for document in origin_documents)
    mapped = clone_rows(db)
    with write_transaction(db):
        restore_local_memory_snapshot(db, "c", "other", remap_checkpoint(snapshot, mapped))
    target_scope = resolve_memory_scope(db, "c", {"session_id": "other"}, {"name": "Mira"}, through_rowid=mapped[last])
    text = read_episodic_block(db, target_scope, "silver key").text
    assert "tower" in text and "pocket" in text and "LEGACY" not in text
    assert read_summary_block(db, target_scope).text == "Public tower"
    assert (
        db.execute("SELECT accepted_after_rowid FROM memory_explicit_events WHERE session_id='other'").fetchone()[0]
        == mapped[first]
    )
    target_index = db.execute("SELECT document_id,state FROM memory_fact_index WHERE session_id='other'").fetchall()
    assert len(target_index) == 2 and all(state == "pending" for _, state in target_index)
    assert not origin_documents.intersection(document for document, _ in target_index)
    sources = db.execute(
        "SELECT source_document_id FROM memory_fact_provenance "
        "WHERE session_id='other' AND source_document_id IS NOT NULL"
    ).fetchall()
    assert sources and all(document.startswith("session:other:source:") for (document,) in sources)
    db.execute("UPDATE messages SET content='Changed only in the origin.' WHERE id=?", (first,))
    db.commit()
    assert "tower" in read_episodic_block(db, target_scope, "silver key").text


def test_snapshot_omits_equal_anchor_explicit_and_unclassified_history(db):
    from bridge.memory_snapshot_store import snapshot_local_memory

    first = append(db)
    remember_local_fact(db, "c", "s", "Mira", "The silver key is in my pocket.")
    with write_transaction(db):
        db.execute("INSERT INTO session_summaries VALUES('c','s','OPAQUE',?,1)", (first,))
    result = snapshot_local_memory(db, "c", "s", first)
    assert result["episodic"] == [] and result["summary"] is None


def test_restored_sparse_fact_sources_do_not_ack_unprocessed_target_prefix(db):
    from bridge.memory_snapshot_store import restore_local_memory_snapshot, snapshot_local_memory

    first = append(db, "An earlier empty extraction.")
    accept_source_facts(db, next_source_segment(db, "c", "s", "episodes"), [])
    last = append(db)
    accept(db)
    snapshot = snapshot_local_memory(db, "c", "s", last)
    mapped = clone_rows(db)
    with write_transaction(db):
        restore_local_memory_snapshot(db, "c", "other", remap_checkpoint(snapshot, mapped))
    source = next_source_segment(db, "c", "other", "episodes")
    assert source is not None and source.start_id == mapped[first]


def test_corrupted_copied_source_cannot_authorize_target_fact(db):
    from bridge.memory_snapshot_store import restore_local_memory_snapshot, snapshot_local_memory

    first = append(db)
    accept(db)
    snapshot = snapshot_local_memory(db, "c", "s", first)
    mapped = clone_rows(db)
    db.execute("UPDATE messages SET content='Different copy.' WHERE id=?", (mapped[first],))
    db.commit()
    with write_transaction(db):
        restore_local_memory_snapshot(db, "c", "other", remap_checkpoint(snapshot, mapped))
    target_scope = resolve_memory_scope(db, "c", {"session_id": "other"}, {"name": "Mira"})
    assert read_episodic_block(db, target_scope, "silver key").text == ""


def test_snapshot_bounds_complete_serialized_proof_without_losing_explicit_events(db):
    import pytest

    from bridge.memory_snapshot_repository import snapshot_local_memory as raw_snapshot
    from bridge.memory_snapshot_store import snapshot_local_memory

    append(db)
    with write_transaction(db):
        for index in range(250):
            remember_local_fact(db, "c", "s", "Mira", f"{index:04d} " + "x" * 3995)
    last = append(db, "Later snapshot boundary.")
    assert len(raw_snapshot(db, "c", "s", last)["episodic"]) == 250
    with pytest.raises(ValueError, match="bounded pre-finale snapshot"):
        snapshot_local_memory(db, "c", "s", last)
    assert db.execute("SELECT count(*) FROM memory_explicit_events WHERE valid=1").fetchone()[0] == 250
