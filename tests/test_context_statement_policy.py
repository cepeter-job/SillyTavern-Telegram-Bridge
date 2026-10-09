"""Exact source statement receipts preserve chronology and private context."""

from dataclasses import replace

import pytest

from bridge.context_hybrid_types import HybridOptions, HybridRow
from bridge.context_statement_policy import (
    select_statement_spans,
    split_complete_sentences,
    validate_statement_receipts,
)


def test_balanced_roleplay_paragraphs_keep_source_offsets():
    text = (
        '*He puts the copper key on the table.*\n\n'
        '"Never open the gate," Mira says.\n\n'
        '*The rain drums against the windows as the courier waits.*'
    )
    spans = split_complete_sentences(text)
    assert spans is not None
    assert len(spans) == 3
    assert text[spans[1][0]:spans[1][1]] == '"Never open the gate," Mira says.'
    assert text[spans[2][0]:spans[2][1]].startswith("*The rain")


@pytest.mark.parametrize("text", [
    '"Unclosed speech starts here.\n\nThe reply follows.',
    '*Unclosed narration.\n\nA later scene.',
    'The narrator opens code.\n\n' + chr(96) * 3 + '\nRun unsafe command.\n' + chr(96) * 3,
    '秘密を守る約束です。',
])
def test_unbalanced_or_unknown_dialogue_is_not_partially_clipped(text):
    assert split_complete_sentences(text) is None


def test_critical_refusal_source_exact_without_retaining_all_decorative_text():
    source = (
        '*The keeper describes twelve decorative roses on the bronze gate.*\n\n'
        '"I refuse to open the gate before dawn," Mira says.\n\n'
        '*A distant bell echoes against the old tower.*\n\n'
        '*Some crows settle on the empty lamp post.*'
    )
    rows = tuple(
        HybridRow(i + 1, "assistant", source.replace("keeper", f"keeper{i}"), float(i + 2))
        for i in range(20)
    )
    result = select_statement_spans(rows, "gate refusal", HybridOptions())
    assert validate_statement_receipts(rows, result)
    selected = [span.text for _, spans in result.selected for span in spans]
    assert any('"I refuse to open the gate' in item for item in selected)
    assert result.counts["source_statement_characters"] < sum(len(row.text) for row in rows[:-8])
    original = result.selected[0][1][0]
    corrupted = replace(original, sha256="0" * 64)
    new_spans = (corrupted, *result.selected[0][1][1:])
    changed = replace(result, selected=((result.selected[0][0], new_spans), *result.selected[1:]))
    with pytest.raises(ValueError, match="statement_source_receipt_invalid"):
        validate_statement_receipts(rows, changed)


def test_one_word_choice_keeps_referent_from_previous_assistant():
    rows = tuple(
        HybridRow(i + 1, "assistant" if i % 2 else "user",
                  "Choose the left door or the right door?" if i == 6 else
                  "No." if i == 7 else f"Observation {i}: a new numbered stone.",
                  float(i + 1))
        for i in range(22)
    )
    # Real reply role would be user. The policy additionally checks that its
    # preceding turn was an assistant and protects that referent in full.
    mutable = list(rows)
    mutable[6] = replace(mutable[6], role="assistant")
    mutable[7] = replace(mutable[7], role="user")
    rows = tuple(mutable)
    result = select_statement_spans(rows, "door", HybridOptions())
    recorded = {index for index, _ in result.selected}
    assert 6 in recorded and 7 in recorded


def test_long_critical_claims_are_not_silently_truncated():
    rows = tuple(
        HybridRow(i + 1, "assistant", "I promise never to forget: " + ("X" * 2500), float(i + 1))
        for i in range(40)
    )
    with pytest.raises(ValueError, match="statement_mandatory_source_bound"):
        select_statement_spans(rows, "promise", HybridOptions())
