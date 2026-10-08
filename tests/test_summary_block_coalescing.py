"""Source-faithful normalization for overlong classified summary block inventories."""

import pytest

from bridge.summary_block_coalescing import coalesce_summary_response


def block(index, *, visibility="shared", known_by=None, text=None):
    return {
        "text": f"Fact {index:02d}: promise, cause, and consequence" if text is None else text,
        "visibility": visibility,
        "known_by": [] if known_by is None else known_by,
    }


def test_adjacent_blocks_are_combined_without_text_loss_or_reordering():
    rows = [block(i) for i in range(31)]
    rows += [block(31, visibility="restricted", known_by=["Alice"])]
    rows += [block(32, visibility="restricted", known_by=["Alice"])]
    rows += [block(33, visibility="shared"), block(34, visibility="shared")]
    result = coalesce_summary_response({"blocks": rows, "other": 123})
    final = result["blocks"]
    assert len(final) == 32
    assert "\n".join(entry["text"] for entry in final) == "\n".join(entry["text"] for entry in rows)
    assert result["other"] == 123
    for row in final:
        assert len(row["text"]) <= 5000
        assert (row["visibility"] == "shared" and row["known_by"] == []) or (
            row["visibility"] == "restricted" and row["known_by"] == ["alice"]
        )


def test_reader_knowledge_stays_in_separate_audience_lanes():
    rows = [block(i) for i in range(33)]
    rows[4] = block(4, visibility="restricted", known_by=["Rowan"])
    rows[5] = block(5, visibility="restricted", known_by=["Mira"])
    rows[6] = block(6, visibility="restricted", known_by=["Rowan"])
    final = coalesce_summary_response({"blocks": rows})["blocks"]
    assert len(final) == 32
    assert "\n".join(x["text"] for x in final) == "\n".join(x["text"] for x in rows)
    assert len([x for x in final if x["visibility"] == "restricted"]) == 3
    assert {tuple(x["known_by"]) for x in final if x["visibility"] == "restricted"} == {("rowan",), ("mira",)}


def test_fully_interleaved_different_audiences_fail_closed():
    rows = [block(i, visibility="restricted", known_by=["Alice" if i % 2 else "Bob"]) for i in range(35)]
    with pytest.raises(ValueError, match="at most 32"):
        coalesce_summary_response({"blocks": rows})


def test_can_only_merge_when_result_stays_within_single_block_char_limit():
    rows = [block(i, text="X" * 4900 if i in (0, 1) else f"Fact {i}") for i in range(35)]
    final = coalesce_summary_response({"blocks": rows})["blocks"]
    assert len(final) == 32
    assert final[0]["text"] == "X" * 4900
    assert final[1]["text"] == "X" * 4900


@pytest.mark.parametrize(
    "blocks",
    [
        [block(i) for i in range(65)],
        [*([block(i) for i in range(34)]), block(34, visibility="shared", known_by=["Alice"])],
        [*([block(i) for i in range(34)]), {"text": 17, "visibility": "shared", "known_by": []}],
        [*([block(i) for i in range(34)]), block(34, text="")],
    ],
)
def test_oversize_or_invalid_audience_or_text_must_not_publish(blocks):
    with pytest.raises(ValueError):
        coalesce_summary_response({"blocks": blocks})


def test_small_payload_is_exactly_unchanged_and_existing_validator_remains_owner():
    payload = {"blocks": [block(1)]}
    result = coalesce_summary_response(payload)
    assert result is payload
    assert coalesce_summary_response({"summary": "Legacy shape"}) == {"summary": "Legacy shape"}


def test_no_history_or_private_source_content_written_to_diagnostics():
    raw = {"blocks": [block(i, text=f"PRIVATE_CANARY_{i}") for i in range(35)]}
    result = coalesce_summary_response(raw)
    assert sum("PRIVATE_CANARY_" in x["text"] for x in result["blocks"]) > 0
    assert len(result["blocks"]) == 32
