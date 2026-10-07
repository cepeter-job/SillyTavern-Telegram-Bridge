"""Only accepted local summaries cross the remote indexing boundary."""

import json
from types import SimpleNamespace

import pytest
from application_test_setup import make_test_provider_port
from settings_test_support import make_test_settings
from test_story_memory_scope import append
from test_story_memory_scope import db as db
from test_story_memory_scope import forbid_real_hindsight_client as forbid_real_hindsight_client

from bridge import memory_backend
from bridge.memory_fact_store import remember_local_fact
from bridge.memory_store import claim_jobs
from bridge.memory_workers import run_memory_claim


def run(db, layer="hindsight", **kwargs):
    claim = claim_jobs(db, layers=(layer,))[0]
    return run_memory_claim(
        db,
        claim,
        {"session_id": "s", "model_id": "m"},
        {"name": "Mira"},
        app_settings=make_test_settings(),
        **kwargs,
    )


def test_long_message_is_complete_local_evidence_and_only_summaries_are_retained(db, monkeypatch):
    text = "a" * 12000 + "b" * 12000 + "c" * 997 + "TAIL"
    append(db, text)
    summaries = iter(("First accepted fact.", "Second accepted fact.", "Tail accepted fact."))
    provider = make_test_provider_port(
        generate_backend=lambda *a, **k: json.dumps(
            [{"kind": "fact", "importance": 0.9, "summary": next(summaries), "visibility": "shared", "known_by": []}]
        )
    )
    assert run(db, "episodes", provider_port=provider) == "complete"
    parts = db.execute(
        "SELECT start_offset,end_offset FROM memory_segments WHERE layer='episodes' AND valid=1 ORDER BY start_offset"
    ).fetchall()
    assert parts == [(0, 12000), (12000, 24000), (24000, 25001)]
    assert "".join(text[start:end] for start, end in parts) == text
    calls = []
    monkeypatch.setattr(memory_backend, "_retain_with_client", lambda *a, **k: calls.append((a, k)) or True)
    assert run(db) == "complete"
    assert [(a[6], a[4]) for a, _ in calls] == [
        ("native_fact", "First accepted fact."),
        ("native_fact", "Second accepted fact."),
        ("native_fact", "Tail accepted fact."),
    ]
    assert all(k["generation_tags"] for _, k in calls)
    assert db.execute("SELECT count(*) FROM memory_segments WHERE layer='hindsight'").fetchone() == (0,)
    assert db.execute("SELECT count(*) FROM hindsight_documents WHERE kind='source_segment'").fetchone() == (0,)
    assert db.execute("SELECT covered_id FROM memory_layer_state WHERE layer='hindsight'").fetchone() == (0,)


def test_transcript_only_job_completes_once_without_external_work(db, monkeypatch):
    append(db)
    calls = []
    monkeypatch.setattr(memory_backend, "_retain_with_client", lambda *a, **k: calls.append(a) or True)
    assert run(db) == "complete"
    assert calls == []
    for _ in range(3):
        assert claim_jobs(db, layers=("hindsight",)) == []
    assert db.execute("SELECT count(*) FROM memory_segments WHERE layer='hindsight'").fetchone() == (0,)


def test_native_indexing_uses_eight_calls_per_claim_and_stable_ids(db, monkeypatch):
    append(db)
    for number in range(10):
        remember_local_fact(db, "c", "s", "Mira", f"Accepted fact {number}.")
    expected = set(db.execute("SELECT document_id FROM memory_fact_index").fetchall())
    calls = []
    monkeypatch.setattr(memory_backend, "_retain_with_client", lambda *a, **k: calls.append(a) or True)
    assert run(db) == "deferred"
    assert len(calls) == 8
    assert all(a[6] == "native_fact" for a in calls)
    assert run(db) == "complete"
    assert len(calls) == 10 and {(a[2],) for a in calls} == expected
    assert db.execute("SELECT count(*) FROM memory_fact_index WHERE state='pending'").fetchone() == (0,)
    assert claim_jobs(db, layers=("hindsight",)) == []


def test_historical_delete_failure_does_not_block_native_indexing(db, monkeypatch):
    append(db)
    remember_local_fact(db, "c", "s", "Mira", "Current silver key.")
    db.execute("INSERT INTO memory_retired_documents(chat_id,session_id,document_id) VALUES('c','s','historical-raw')")
    db.commit()

    async def failed_delete(**kwargs):
        raise RuntimeError("Historical DELETE is unavailable")

    client = SimpleNamespace(documents=SimpleNamespace(delete_document=failed_delete), close=lambda: None)
    monkeypatch.setattr(memory_backend, "hindsight_client", lambda **k: client)
    calls = []
    monkeypatch.setattr(memory_backend, "_retain_with_client", lambda *a, **k: calls.append(a) or True)
    assert run(db) == "complete"
    assert [a[4] for a in calls] == ["Current silver key."]
    assert db.execute("SELECT state FROM memory_fact_index").fetchone() == ("retained",)
    assert db.execute("SELECT deleted FROM memory_retired_documents").fetchone() == (0,)


@pytest.mark.parametrize("kind", ["source_segment", "conversation", "curated"])
def test_generic_retain_rejects_non_native_calls_before_client_creation(db, monkeypatch, kind):
    monkeypatch.setattr(memory_backend, "hindsight_client", lambda **k: pytest.fail("Raw client constructed"))
    with pytest.raises(ValueError, match="native"):
        memory_backend._retain_with_client(
            "c",
            "s",
            "historical",
            "Mira",
            "Transcript",
            "Transcript",
            kind,
            "%s",
            app_settings=make_test_settings(),
        )


def test_off_mode_keeps_local_authority_then_indexes_pending_facts(db, monkeypatch):
    from bridge.memory_scope_store import read_episodic_block, resolve_memory_scope

    remember_local_fact(db, "c", "s", "Mira", "A durable silver key.")
    db.execute("INSERT INTO meta(key,value) VALUES('memory_mode:c','off')")
    db.commit()
    monkeypatch.setattr(memory_backend, "hindsight_client", lambda **k: pytest.fail("Disabled client constructed"))
    assert run(db) == "disabled"
    scope = resolve_memory_scope(db, "c", {"session_id": "s"}, {"name": "Mira"})
    assert "silver key" in read_episodic_block(db, scope, "key").text
    assert (
        memory_backend.recall_memory_context(
            db, "c", {"session_id": "s"}, {"name": "Mira"}, "key", app_settings=make_test_settings()
        )
        == ""
    )
    db.execute("UPDATE meta SET value='on' WHERE key='memory_mode:c'")
    db.execute("UPDATE memory_jobs SET next_attempt_at=0")
    db.commit()
    calls = []
    monkeypatch.setattr(memory_backend, "_retain_with_client", lambda *a, **k: calls.append(a) or True)
    assert run(db) == "complete"
    assert [a[4] for a in calls] == ["A durable silver key."]
