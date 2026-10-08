"""Selection uses accepted local evidence and falls back on canonical changes."""

from dataclasses import replace

import pytest
from settings_test_support import make_test_settings
from test_story_memory_scope import accept, append, scope
from test_story_memory_scope import db as db

from bridge.context_selection import select_memory_blocks
from bridge.context_selection_store import prepare_context_selection
from bridge.memory_artifact_store import read_summary_block, store_artifact_visibility
from bridge.memory_composition import build_memory_service
from bridge.memory_contracts import MemoryPromptContext
from bridge.memory_fact_store import remember_local_fact
from bridge.memory_scope_store import ranked_fact_block, read_episodic_block, validate_memory_blocks
from bridge.memory_store import next_source_segment, purge_external_memory, store_segment
from bridge.sqlite_store import write_transaction


def test_native_fact_payload_mapping_deduplicates_without_reparsing_the_prompt(db):
    append(db, "The silver key is not in the tower because Bob moved it.")
    accept(db, text="The silver key is not in the tower because Bob moved it.")
    db.execute("UPDATE memory_fact_index SET state='retained'")
    db.commit()
    document = db.execute("SELECT document_id FROM memory_fact_index").fetchone()[0]
    captured = scope(db)
    baseline = read_episodic_block(db, captured, "silver key")
    recall = ranked_fact_block(db, captured, [document])
    result = select_memory_blocks(captured, (recall, baseline))
    assert result.blocks[0].text == (
        "[fact] The silver key is not in the tower because Bob moved it. (source: message 1, chars 0:56; fact 1)"
    )
    assert result.blocks[1].text == ""
    assert result.deduplicated_blocks == 1
    assert baseline.text == recall.text


def test_accepted_artifact_records_keep_exact_audience_and_payload_boundaries(db):
    row = append(db, "Mira knows the key is in the tower. Bob sees only the tower.")
    with write_transaction(db):
        db.execute("INSERT INTO session_summaries VALUES('c','s','canonical artifact',?,1)", (row,))
        store_artifact_visibility(
            db,
            "c",
            "s",
            "summary",
            [
                {"text": "The tower exists.", "visibility": "shared", "known_by": []},
                {"text": "The key is in the tower.", "visibility": "restricted", "known_by": ["Mira"]},
            ],
        )
    captured = scope(db)
    block = read_summary_block(db, captured)
    assert block.text == "The tower exists.\nThe key is in the tower."
    assert tuple((leaf.text, leaf.visibility, leaf.known_by) for leaf in block.leaves) == (
        ("The tower exists.", "shared", ()),
        ("The key is in the tower.", "restricted", ("mira",)),
    )
    assert tuple(leaf.evidence for leaf in block.leaves) == block.evidence
    assert all(leaf.scope == captured for leaf in block.leaves)
    result = select_memory_blocks(captured, (block, replace(block)))
    assert tuple(item.text for item in result.blocks) == (
        "The tower exists.\nThe key is in the tower.",
        "The tower exists.\nThe key is in the tower.",
    )


def configured_service(mode="enabled", slices="dedup"):
    settings = make_test_settings(
        environ={"SILLYTAVERN_CONTEXT_SELECTION_MODE": mode, "SILLYTAVERN_CONTEXT_SELECTION_SLICES": slices}
    )
    service, _runtime = build_memory_service(
        settings,
        load_group_state=lambda *_args: {},
        retain_session=lambda *_args: None,
    )
    return service


def memory_context(db, *, mode="enabled", slices="dedup", fields=None, **kwargs):
    return configured_service(mode, slices).prompt_context(
        db, "c", {"session_id": "s"}, fields if fields is not None else {"name": "Mira"}, "silver key", **kwargs
    )


@pytest.mark.parametrize("mode", ["off", "shadow", "enabled"])
def test_rollout_metadata_never_changes_the_baseline_memory_strings(db, mode):
    append(db)
    accept(db)
    context = memory_context(db, mode=mode)
    assert context.episodic == "[fact] The silver key is hidden in the tower. (source: message 1, chars 0:38; fact 1)"
    assert (context.recall, context.summary, context.scene) == ("", "", "")
    assert getattr(context, "selection_mode", "missing") == mode
    assert tuple(item.channel for item in context.baseline_blocks) == ("recall", "episodic", "summary", "scene")
    if mode == "off":
        assert context.selection_guard is None
        assert context.selection is None
        assert context.selection_reason == "off"
    else:
        assert context.selection is not None
        assert context.selection.blocks == context.baseline_blocks
        assert context.selection_guard() == ""


def test_enabled_without_an_approved_memory_slice_does_not_create_a_candidate(db):
    append(db)
    accept(db)
    context = memory_context(db, slices="summary")
    assert getattr(context, "selection_reason", "missing") == "not_approved"
    assert context.selection is None
    assert context.selection_guard is None


@pytest.mark.parametrize(
    ("race", "reason"),
    [
        ("rewrite", "source_changed"),
        ("append", "source_changed"),
        ("audience", "source_changed"),
        ("recreate", "invalid_scope"),
        ("purge", "source_changed"),
        ("explicit", "source_changed"),
        ("reader", "source_changed"),
        ("pending", "pending_invalidation"),
    ],
)
def test_dispatch_guard_rejects_changed_canonical_sources_and_reader_boundaries(db, race, reason):
    first = append(db)
    accept(db)
    fields = {"name": "Mira"}
    context = memory_context(db, fields=fields)
    guard = getattr(context, "selection_guard", None)
    assert callable(guard), "A candidate requires a fresh canonical guard at dispatch"
    if race == "rewrite":
        db.execute("UPDATE messages SET content='No key exists.' WHERE id=?", (first,))
    elif race == "append":
        append(db, "A later event changes the active turn.")
    elif race == "audience":
        db.execute("UPDATE episodic_memory_visibility SET known_by_json='[\"bob\"]'")
    elif race == "recreate":
        db.execute("UPDATE sessions SET created_at=99 WHERE chat_id='c' AND session_id='s'")
    elif race == "purge":
        purge_external_memory(db, "c", "s", purge_epoch=1)
    elif race == "explicit":
        remember_local_fact(db, "c", "s", "Mira", "The key is now in the garden.")
    elif race == "reader":
        fields["name"] = "Bob"
    else:
        db.execute("UPDATE memory_layer_state SET invalidated_from_id=1 WHERE layer='episodes'")
    db.commit()
    assert guard() == reason
    assert "silver key" in context.episodic


def test_historical_selection_is_an_explicit_unchanged_fallback(db):
    row = append(db)
    accept(db)
    context = memory_context(db, through_rowid=row)
    assert getattr(context, "selection_reason", "missing") == "historical"
    assert "silver key" in context.episodic
    assert context.selection is None
    assert callable(context.selection_guard)


def test_initial_revocation_keeps_a_guard_for_the_final_dispatch(db):
    append(db)
    accept(db)
    captured = memory_context(db, mode="off")
    db.execute("UPDATE episodic_memory_visibility SET known_by_json='[\"bob\"]'")
    db.commit()
    context = prepare_context_selection(
        db,
        captured,
        lambda: scope(db),
        app_settings=make_test_settings({"SILLYTAVERN_CONTEXT_SELECTION_MODE": "shadow"}),
        validate_blocks=validate_memory_blocks,
    )
    assert context.selection_reason == "source_changed"
    assert callable(context.selection_guard)
    assert context.selection_guard() == "source_changed"


@pytest.mark.parametrize("coverage", ["missing", "partial", "accepted"])
def test_history_never_treats_checkpoint_coverage_as_causal_sufficiency(db, coverage):
    row = append(db, "Mira did not give Bob the key because Bob still owed her a debt.")
    with write_transaction(db):
        db.execute("INSERT INTO session_summaries VALUES('c','s','They discussed the key.',?,1)", (row,))
        store_artifact_visibility(
            db,
            "c",
            "s",
            "summary",
            [{"text": "They discussed the key.", "visibility": "restricted", "known_by": ["Mira"]}],
        )
    if coverage != "missing":
        source = next_source_segment(db, "c", "s", "summary", max_chars=12 if coverage == "partial" else 12000)
        assert store_segment(db, source)
    context = memory_context(db, slices="history")
    expected = "ambiguous" if coverage == "accepted" else "incomplete_coverage"
    assert context.selection_reason == expected
    assert context.selection_coverage_valid is (coverage == "accepted")
    assert context.summary == "They discussed the key."
    assert context.selection.blocks == context.baseline_blocks
    assert db.execute("SELECT content FROM messages WHERE id=?", (row,)).fetchone()[0] == (
        "Mira did not give Bob the key because Bob still owed her a debt."
    )


def test_history_requires_a_checkpoint_visible_to_the_actual_reader(db):
    row = append(db)
    with write_transaction(db):
        db.execute("INSERT INTO session_summaries VALUES('c','s','A private key fact.',?,1)", (row,))
        store_artifact_visibility(
            db,
            "c",
            "s",
            "summary",
            [{"text": "A private key fact.", "visibility": "restricted", "known_by": ["Bob"]}],
        )
    assert store_segment(db, next_source_segment(db, "c", "s", "summary"))
    context = memory_context(db, slices="history")
    assert context.selection_reason == "incomplete_coverage"
    assert context.summary == ""
    assert context.selection.blocks == context.baseline_blocks


def test_history_coverage_rejects_a_gap_behind_an_accepted_watermark(db):
    append(db, "Mira owes Bob a debt.")
    row = append(db, "The key is a callback to that debt.")
    first = next_source_segment(db, "c", "s", "summary")
    assert store_segment(db, first)
    assert store_segment(db, next_source_segment(db, "c", "s", "summary"))
    with write_transaction(db):
        db.execute("INSERT INTO session_summaries VALUES('c','s','The key.',?,1)", (row,))
        store_artifact_visibility(
            db,
            "c",
            "s",
            "summary",
            [{"text": "The key.", "visibility": "shared", "known_by": []}],
        )
        db.execute("DELETE FROM memory_segments WHERE document_id=?", (first.document_id,))
    context = memory_context(db, slices="history")
    assert context.selection_reason == "incomplete_coverage"
    assert not context.selection_coverage_valid
    assert context.selection.blocks == context.baseline_blocks


def test_unapproved_dedup_preserves_both_payloads_and_the_selected_count(db):
    append(db)
    accept(db)
    captured = scope(db)
    evidence = read_episodic_block(db, captured, "silver key")
    context = MemoryPromptContext(scope=captured, baseline_blocks=(evidence, evidence))

    def validate_independent_channels(connection, reader, blocks):
        return tuple(validate_memory_blocks(connection, reader, (block,))[0] for block in blocks)

    prepared = prepare_context_selection(
        db,
        context,
        lambda: scope(db),
        app_settings=make_test_settings(
            environ={"SILLYTAVERN_CONTEXT_SELECTION_MODE": "enabled", "SILLYTAVERN_CONTEXT_SELECTION_SLICES": "history"}
        ),
        validate_blocks=validate_independent_channels,
    )
    assert prepared.selection.blocks == (evidence, evidence)
    assert prepared.selection.selected_blocks == 2
    assert prepared.selection.deduplicated_blocks == 0
