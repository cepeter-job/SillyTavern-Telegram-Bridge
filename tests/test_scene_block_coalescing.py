"""Scene capacity recovery must preserve narrative text, audiences and coverage."""

import json
import logging

import pytest
from application_test_setup import make_test_provider_port
from memory_runtime_test_support import isolated_memory_runtime as isolated_memory_runtime
from session_test_support import session_db as session_db
from test_durable_memory_workers import add

from bridge.memory_artifact_store import load_artifact_classification
from bridge.memory_store import claim_jobs
from bridge.memory_workers import run_memory_claim
from bridge.scene_state import get_scene_state


def block(index, *, knower=None, length=None):
    text = f"PRIVATE_FACT_{index:02d}: promise and consequence."
    if length is not None:
        text = text.ljust(length, "x")
    return {"text": text, "visibility": "restricted" if knower else "shared", "known_by": [knower] if knower else []}


def run_scene(session_db, payload, calls):
    settings, db, session = session_db

    def generate(*args, **kwargs):
        calls.append(args[2])
        return json.dumps(payload)

    return run_memory_claim(
        db,
        claim_jobs(db, layers=("scene",))[0],
        session,
        {"name": "Alice"},
        provider_port=make_test_provider_port(generate_backend=generate),
        app_settings=settings,
    )


def parsed_events(caplog):
    return [
        record.diagnostic_fields
        for record in caplog.records
        if getattr(record, "diagnostic_fields", {}).get("event") == "scene.contract_parsed"
    ]


@pytest.mark.parametrize("count,total_chars", [(35, None), (64, None), (33, 31999)])
def test_oversized_scene_is_published_losslessly_with_original_audiences(session_db, caplog, count, total_chars):
    _, db, _ = session_db
    add(db, "PRIVATE_SOURCE_CANARY")
    caplog.set_level(logging.INFO, logger="bridge.events")
    if total_chars is None:
        rows = [block(i, knower="Alice" if i % 8 in (4, 5) else None) for i in range(count)]
    else:
        rows = [block(i, length=total_chars // count) for i in range(count)]
        rows[0]["text"] += "x" * (total_chars % count)
    original = json.dumps(rows)
    calls = []

    assert run_scene(session_db, {"state": {"location": "Established place"}, "blocks": rows}, calls) == "complete"

    assert len(calls) == 1, "valid lossless packing must not spend a model repair attempt"
    classification = load_artifact_classification(db, "chat", "s1", "scene")
    assert classification is not None
    _, through, final = classification
    assert len(final) == 32
    assert "\n".join(item["text"] for item in final) == "\n".join(item["text"] for item in rows)
    assert all(len(item["text"]) <= 5000 for item in final)
    assert sum(len(item["text"]) for item in final) <= 32000
    for item in rows:
        owners = [result for result in final if item["text"] in result["text"]]
        assert len(owners) == 1
        assert owners[0]["visibility"] == item["visibility"]
        assert owners[0]["known_by"] == [name.lower() for name in item["known_by"]]
    assert json.dumps(rows) == original
    assert get_scene_state(db, "chat", "s1") == ({"location": "Established place"}, through)
    assert through == db.execute("SELECT max(rowid) FROM messages").fetchone()[0]
    assert db.execute("SELECT covered_id FROM memory_layer_state WHERE layer='scene'").fetchone() == (through,)
    assert db.execute("SELECT dirty_version=completed_version FROM memory_jobs WHERE layer='scene'").fetchone() == (1,)
    observed = parsed_events(caplog)
    assert len(observed) == 1 and observed[0]["accepted"] is True
    assert observed[0]["count"] == count
    assert "PRIVATE" not in caplog.text and "PRIVATE" not in repr(observed)


def test_scene_finds_valid_partition_when_shortest_pair_would_block_it(session_db):
    _, db, _ = session_db
    add(db, "Canonical scene source")
    rows = [block(i, length=size) for i, size in enumerate((3000, 1500, 1500, 3000))]
    rows += [block(i + 4, knower="Alice" if i % 2 else "Bob") for i in range(30)]
    calls = []

    assert run_scene(session_db, {"state": {}, "blocks": rows}, calls) == "complete"

    classification = load_artifact_classification(db, "chat", "s1", "scene")
    assert classification is not None
    final = classification[2]
    assert len(calls) == 1 and len(final) == 32
    assert [row["text"] for row in final[:2]] == [
        rows[0]["text"] + "\n" + rows[1]["text"],
        rows[2]["text"] + "\n" + rows[3]["text"],
    ]
    assert "\n".join(row["text"] for row in final) == "\n".join(row["text"] for row in rows)
    assert [(row["visibility"], row["known_by"]) for row in final[2:]] == [
        (row["visibility"], [name.lower() for name in row["known_by"]]) for row in rows[4:]
    ]


def accepted_scene(session_db):
    _, db, _ = session_db
    add(db, "Previously accepted source")
    assert run_scene(session_db, {"state": {"location": "Original place"}, "blocks": [block(0)]}, []) == "complete"
    before = (get_scene_state(db, "chat", "s1"), load_artifact_classification(db, "chat", "s1", "scene"))
    add(db, "PRIVATE_NEW_SOURCE")
    return before


def assert_publication_unchanged(db, before):
    assert (get_scene_state(db, "chat", "s1"), load_artifact_classification(db, "chat", "s1", "scene")) == before
    assert db.execute("SELECT covered_id FROM memory_layer_state WHERE layer='scene'").fetchone() == (before[0][1],)
    assert db.execute("SELECT dirty_version>completed_version FROM memory_jobs WHERE layer='scene'").fetchone() == (1,)


@pytest.mark.parametrize(
    "rows,code",
    [
        ([block(i, knower="Alice" if i % 2 else "Bob") for i in range(33)], "scene_blocks_unmergeable"),
        ([block(i) for i in range(65)], "scene_blocks_raw_limit"),
        ([block(i, length=1000) for i in range(33)], "scene_total_text_limit"),
        ([block(i, length=969) for i in range(32)] + [block(32, length=992)], "scene_total_text_limit"),
    ],
)
def test_unrecoverable_scene_capacity_preserves_publication_and_reports_only_bounds(session_db, caplog, rows, code):
    _, db, _ = session_db
    before = accepted_scene(session_db)
    caplog.set_level(logging.INFO, logger="bridge.events")
    caplog.clear()
    calls = []

    result = run_scene(session_db, {"state": {"location": "PRIVATE_REJECTED"}, "blocks": rows}, calls)

    assert result == "invalid_shape"
    assert len(calls) == 2
    assert_publication_unchanged(db, before)
    observed = parsed_events(caplog)
    assert len(observed) == 2
    assert all(item["accepted"] is False and item["rejection_code"] == code for item in observed)
    assert all(item["count"] == len(rows) for item in observed)
    assert code in calls[1][0]["content"]
    assert calls[0][-1] == calls[1][-1], "format recovery must retain the same canonical input"
    assert "PRIVATE" not in caplog.text and "PRIVATE" not in repr(observed)


@pytest.mark.parametrize(
    "invalid,expected,calls_count",
    [
        ({"text": 42, "visibility": "shared", "known_by": []}, "invalid_shape", 2),
        ({"text": "", "visibility": "shared", "known_by": []}, "invalid_shape", 2),
        ({"text": "x" * 5001, "visibility": "shared", "known_by": []}, "invalid_shape", 2),
        ({"text": "PRIVATE_SECRET", "visibility": "shared", "known_by": ["Alice"]}, "invalid_audience", 1),
        ({"text": "PRIVATE_SECRET", "visibility": "restricted", "known_by": []}, "invalid_audience", 1),
    ],
)
def test_overflow_never_hides_malformed_original_text_or_audience(session_db, invalid, expected, calls_count):
    _, db, _ = session_db
    before = accepted_scene(session_db)
    rows = [block(i) for i in range(33)] + [invalid]
    calls = []

    assert run_scene(session_db, {"state": {}, "blocks": rows}, calls) == expected

    assert len(calls) == calls_count
    assert_publication_unchanged(db, before)
