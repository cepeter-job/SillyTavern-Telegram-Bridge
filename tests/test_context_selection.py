"""Behavioral checks for optional, provenance-preserving memory selection."""

import importlib
from dataclasses import replace
from types import SimpleNamespace

import pytest

from bridge import memory_contracts
from bridge.memory_contracts import MemoryBlock, MemoryEvidence, MemoryReadScope


def policy():
    try:
        return importlib.import_module("bridge.context_selection")
    except ModuleNotFoundError:
        pytest.fail("Context selection must provide an explicit fail-closed rollout policy")


@pytest.mark.parametrize("environment", [{}, {"SILLYTAVERN_CONTEXT_SELECTION_MODE": "unexpected"}])
def test_selection_requires_an_explicit_recognized_mode(environment):
    selection = policy()
    settings = SimpleNamespace(environ=environment)
    assert selection.context_selection_mode(settings) == "off"
    assert not selection.context_slice_enabled(settings, "dedup")


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_observation_and_off_modes_cannot_enable_prompt_mutations(mode):
    selection = policy()
    settings = SimpleNamespace(
        environ={"SILLYTAVERN_CONTEXT_SELECTION_MODE": mode, "SILLYTAVERN_CONTEXT_SELECTION_SLICES": "dedup"}
    )
    assert selection.context_selection_mode(settings) == mode
    assert not selection.context_slice_enabled(settings, "dedup")


def test_enabled_mode_only_approves_explicit_known_slices():
    selection = policy()
    settings = SimpleNamespace(environ={"SILLYTAVERN_CONTEXT_SELECTION_MODE": "enabled"})
    assert not selection.context_slice_enabled(settings, "dedup")
    settings.environ["SILLYTAVERN_CONTEXT_SELECTION_SLICES"] = " dedup,summary,unknown,dedup "
    assert selection.context_slice_enabled(settings, "dedup")
    assert selection.context_slice_enabled(settings, "summary")
    assert not selection.context_slice_enabled(settings, "history")
    assert not selection.context_slice_enabled(settings, "unknown")


def reader_scope(**changes):
    return replace(MemoryReadScope("chat", "story", 1.0, 30, 4, ("mira",), explicit_event_cutoff=10), **changes)


def leaf(text="The key is not in the tower.", **changes):
    assert hasattr(memory_contracts, "MemoryBlockLeaf"), "Canonical payload mapping is required before deduplication"
    values = dict(
        text=text,
        evidence=MemoryEvidence(7, "source-7", 2, 2, 0, 28),
        scope=reader_scope(),
        visibility="restricted",
        known_by=("mira",),
        rewrite_revision=4,
    )
    values.update(changes)
    return memory_contracts.MemoryBlockLeaf(**values)


def block(*leaves, channel="episodic"):
    return MemoryBlock(
        "\n".join(item.text for item in leaves), tuple(item.evidence for item in leaves), channel, leaves
    )


def test_duplicate_removal_uses_canonical_identity_and_preserves_other_payloads():
    first = leaf()
    callback = leaf("Mira owes Bob a key because he rescued her.", evidence=MemoryEvidence(8, "source-8", 3, 3, 0, 41))
    blocks = (block(first, channel="recall"), block(first, callback), MemoryBlock(channel="summary"))
    result = policy().select_memory_blocks(reader_scope(), blocks)
    assert tuple(item.text for item in result.blocks) == (
        "The key is not in the tower.",
        "Mira owes Bob a key because he rescued her.",
        "",
    )
    assert tuple(item.channel for item in result.blocks) == ("recall", "episodic", "summary")
    assert result.selected_blocks == 2
    assert result.deduplicated_blocks == 1
    assert result.reason == "selected"


@pytest.mark.parametrize(
    "changes",
    [
        {"evidence": MemoryEvidence(8, "source-8", 2, 2, 0, 28)},
        {"evidence": MemoryEvidence(7, "source-7", 3, 3, 0, 28)},
        {"evidence": MemoryEvidence(7, "source-7", 2, 2, 1, 29)},
        {"evidence": MemoryEvidence(memory_id=7, explicit_event_id=3)},
        {"known_by": ("bob", "mira")},
        {"visibility": "shared", "known_by": ()},
        {"scope": reader_scope(session_id="other")},
        {"scope": reader_scope(through_rowid=29)},
        {"rewrite_revision": 3},
        {"text": "The key is in the tower."},
    ],
)
def test_equal_words_never_merge_distinct_sources_audiences_boundaries_or_payloads(changes):
    blocks = (block(leaf(), channel="recall"), block(leaf(**changes)))
    result = policy().select_memory_blocks(reader_scope(), blocks)
    assert result.blocks == blocks
    assert result.deduplicated_blocks == 0


def test_remote_retrieval_document_id_is_not_a_new_canonical_source():
    first = leaf()
    remote = replace(first, evidence=replace(first.evidence, document_id="remote-index-reference"))
    result = policy().select_memory_blocks(reader_scope(), (block(remote, channel="recall"), block(first)))
    assert result.blocks[0].text == "The key is not in the tower."
    assert result.blocks[1].text == ""


@pytest.mark.parametrize(
    "pointer",
    [
        MemoryEvidence(),
        MemoryEvidence(memory_id=7),
        MemoryEvidence(7, "source-7", 2, 2, 3, 2),
        MemoryEvidence(memory_id=7, explicit_event_id=3, start_offset=1, end_offset=2),
        MemoryEvidence(7, "source-7", 2, 2, 0, 28, artifact_digest="orphaned-artifact-digest"),
        MemoryEvidence(7, "source-7", 2, 2, 0, 28, block_index=1),
    ],
)
def test_invalid_canonical_pointers_are_never_deduplicated(pointer):
    blocks = (block(leaf(evidence=pointer), channel="recall"), block(leaf(evidence=pointer)))
    result = policy().select_memory_blocks(reader_scope(), blocks)
    assert result.blocks == blocks
    assert result.reason == "ambiguous"


@pytest.mark.parametrize("changes", [{"principals": ()}, {"session_id": ""}, {"through_rowid": -1}])
def test_unknown_reader_scope_is_an_unchanged_fallback(changes):
    blocks = (block(leaf(), channel="recall"), block(leaf()))
    result = policy().select_memory_blocks(reader_scope(**changes), blocks)
    assert result.blocks == blocks
    assert result.reason == "invalid_scope"


def test_historical_reads_keep_the_full_baseline():
    blocks = (block(leaf(), channel="recall"), block(leaf()))
    result = policy().select_memory_blocks(reader_scope(historical=True), blocks)
    assert result.blocks == blocks
    assert result.reason == "historical"


def test_unmapped_and_mismatched_final_text_does_not_grant_deduplication_authority():
    plain = MemoryBlock("The key is not in the tower.", (leaf().evidence,), "episodic")
    changed = replace(block(leaf()), text="The key is in the tower.")
    blocks = (plain, plain, changed, changed)
    result = policy().select_memory_blocks(reader_scope(), blocks)
    assert result.blocks == blocks
    assert result.reason == "ambiguous"


@pytest.mark.parametrize("channel", ["scene", "policy", "dialogue", "simulation"])
def test_protected_channels_are_never_rewritten(channel):
    blocks = (block(leaf(), channel=channel), block(leaf(), channel=channel))
    result = policy().select_memory_blocks(reader_scope(), blocks)
    assert result.blocks == blocks
    assert result.deduplicated_blocks == 0
