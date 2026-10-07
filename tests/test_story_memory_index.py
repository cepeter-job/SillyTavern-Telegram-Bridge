"""Native acceptance and external indexing have separate durable completion."""

import json
from types import SimpleNamespace

import pytest
from application_test_setup import make_test_provider_port
from hindsight_recall_test_support import identity_port
from settings_test_support import make_test_settings
from test_story_memory_scope import accept, append
from test_story_memory_scope import db as db
from test_story_memory_scope import forbid_real_hindsight_client as forbid_real_hindsight_client

from bridge import memory_backend
from bridge.memory_store import claim_jobs, next_source_segment, purge_external_memory
from bridge.memory_workers import run_memory_claim


def run(db, layer, **kwargs):
    claim = claim_jobs(db, layers=(layer,))[0]
    return run_memory_claim(
        db,
        claim,
        {"session_id": "s", "model_id": "m"},
        {"name": "Mira"},
        app_settings=make_test_settings(),
        **kwargs,
    )


def retry(db):
    db.execute("UPDATE memory_jobs SET next_attempt_at=0")
    db.commit()


def test_worker_accepts_source_facts_atomically_and_restart_indexes_without_extraction(db, monkeypatch):
    append(db)
    provider = make_test_provider_port(
        generate_backend=lambda *a, **k: json.dumps(
            [
                {
                    "kind": "fact",
                    "importance": 0.9,
                    "summary": "The silver key is hidden.",
                    "visibility": "restricted",
                    "known_by": ["Mira"],
                }
            ]
        )
    )
    assert run(db, "episodes", provider_port=provider) == "complete"
    assert db.execute("SELECT count(*) FROM memory_fact_provenance").fetchone()[0] == 1
    assert next_source_segment(db, "c", "s", "episodes") is None
    retained = []
    monkeypatch.setattr(memory_backend, "_retain_with_client", lambda *a, **k: retained.append(a) or True)
    assert run(db, "hindsight") == "complete"
    assert db.execute("SELECT state FROM memory_fact_index").fetchone()[0] == "retained"
    native = [args for args in retained if args[6] == "native_fact"]
    assert [args[4] for args in native] == ["The silver key is hidden."]


@pytest.mark.parametrize("reply", ["not json", '{"memories":{}}'])
def test_parser_failure_does_not_ack_source(db, reply):
    append(db)
    provider = make_test_provider_port(generate_backend=lambda *a, **k: reply)
    assert run(db, "episodes", provider_port=provider) == "work_failed"
    assert next_source_segment(db, "c", "s", "episodes") is not None
    assert db.execute("SELECT count(*) FROM episodic_memories").fetchone()[0] == 0


def test_provider_and_late_stale_completion_never_accept_a_part(db):
    append(db)

    def response(*args, **kwargs):
        assert not db.in_transaction
        db.execute("UPDATE messages SET content='Rewritten source'")
        db.commit()
        return '[{"kind":"fact","importance":0.9,"summary":"Old silver key","visibility":"shared","known_by":[]}]'

    provider = make_test_provider_port(generate_backend=response)
    assert run(db, "episodes", provider_port=provider) == "stale_source"
    assert db.execute("SELECT count(*) FROM episodic_memories").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM memory_segments WHERE valid=1").fetchone()[0] == 0


def test_fact_failure_remains_pending_and_retries_same_identity(db, monkeypatch):
    append(db)
    accept(db)
    attempts = []
    monkeypatch.setattr(memory_backend, "_retain_with_client", lambda *a, **k: attempts.append(a[2]) or False)
    assert run(db, "hindsight") == "retain_failed"
    assert db.execute("SELECT state FROM memory_fact_index").fetchone()[0] == "pending"
    assert db.execute("SELECT completed_version FROM memory_jobs WHERE layer='hindsight'").fetchone()[0] == 0
    retry(db)
    monkeypatch.setattr(memory_backend, "_retain_with_client", lambda *a, **k: attempts.append(a[2]) or True)
    assert run(db, "hindsight") == "complete"
    assert len(attempts) == 2 and attempts[0] == attempts[1]


def test_native_fact_arriving_during_old_lease_stays_dirty_and_work_is_bounded(db, monkeypatch):
    from bridge.memory_fact_store import remember_local_fact

    append(db, "x" * 120000)
    accept(db)
    calls = []

    def retain(*args, **kwargs):
        assert not db.in_transaction
        calls.append(args)
        if len(calls) == 1:
            remember_local_fact(db, "c", "s", "Mira", "A late silver key assertion.")
        return True

    monkeypatch.setattr(memory_backend, "_retain_with_client", retain)
    assert run(db, "hindsight") == "deferred"
    assert any(args[6] == "native_fact" for args in calls)
    assert len(calls) <= 8
    dirty, complete = db.execute(
        "SELECT dirty_version,completed_version FROM memory_jobs WHERE layer='hindsight'"
    ).fetchone()
    assert dirty > complete


@pytest.mark.parametrize("mutation", ["rewrite", "purge", "delete"])
def test_late_remote_fact_completion_is_retired_before_admission(db, monkeypatch, mutation):
    from bridge.memory_fact_store import index_fact_is_current

    row = append(db)
    accept(db)
    document = db.execute("SELECT document_id FROM memory_fact_index").fetchone()[0]

    def retain(*args, **kwargs):
        if args[6] == "native_fact":
            if mutation == "rewrite":
                db.execute("UPDATE messages SET content='Replacement' WHERE id=?", (row,))
                db.commit()
            elif mutation == "purge":
                purge_external_memory(db, "c", "s", purge_epoch=1)
            else:
                db.execute("DELETE FROM sessions WHERE chat_id='c' AND session_id='s'")
                db.commit()
        return True

    monkeypatch.setattr(memory_backend, "_retain_with_client", retain)
    assert run(db, "hindsight") == "stale_source"
    assert index_fact_is_current(db, document) is None
    assert db.execute("SELECT state FROM memory_fact_index WHERE document_id=?", (document,)).fetchone()[0] == "retired"
    assert db.execute("SELECT deleted FROM memory_retired_documents WHERE document_id=?", (document,)).fetchone() == (
        0,
    )


def test_public_remember_is_local_during_outage_and_memory_off(db, monkeypatch):
    from bridge.memory_scope_store import read_episodic_block, resolve_memory_scope

    assert memory_backend.remember_fact(
        db,
        "c",
        {"session_id": "s"},
        {"name": "Mira"},
        "The silver key is mine.",
        app_settings=make_test_settings(),
    )
    db.execute("INSERT INTO meta(key,value) VALUES('memory_mode:c','off')")
    db.commit()
    assert run(db, "hindsight") == "disabled"
    scope = resolve_memory_scope(db, "c", {"session_id": "s"}, {"name": "Mira"})
    assert "silver key" in read_episodic_block(db, scope, "key").text


def test_recall_queries_direct_native_facts_and_rehydrates_local_text(db, monkeypatch):
    from hindsight_client_api.models.recall_response import RecallResponse
    from hindsight_client_api.models.recall_result import RecallResult

    append(db)
    _, memory_id = accept(db, audience=(), visibility="shared")
    document = db.execute("SELECT document_id FROM memory_fact_index WHERE memory_id=?", (memory_id,)).fetchone()[0]
    db.execute("UPDATE memory_fact_index SET state='retained'")
    db.commit()
    queries = []

    class Client:
        def recall(self, **kwargs):
            queries.append(kwargs)
            return RecallResponse(
                results=[
                    RecallResult(id="accepted", document_id=document, text="REMOTE SECRET", type="world"),
                    RecallResult(id="raw", document_id="raw-source", text="PRIVATE RAW", type="experience"),
                ]
            )

        def close(self):
            pass

    monkeypatch.setattr(memory_backend, "hindsight_client", lambda **k: Client())
    result = memory_backend.recall_memory_context(
        db,
        "c",
        {"session_id": "s"},
        {"name": "Bob"},
        "silver key",
        app_settings=make_test_settings(),
        remote_recall=identity_port(Client()),
    )
    assert "silver key" in result and "REMOTE" not in result and "PRIVATE" not in result
    assert queries[0]["session_id"] == "s"
    assert queries[0]["query"] == "silver key"


@pytest.mark.parametrize("classification", [{}, {"visibility": "restricted", "known_by": [42]}])
def test_unclassified_or_malformed_audience_never_acknowledges_native_source(db, classification):
    append(db)
    response = {"kind": "fact", "importance": 0.9, "summary": "A secret silver key", **classification}
    provider = make_test_provider_port(generate_backend=lambda *a, **k: json.dumps([response]))
    assert run(db, "episodes", provider_port=provider) == "work_failed"
    assert next_source_segment(db, "c", "s", "episodes") is not None
    assert db.execute("SELECT count(*) FROM memory_fact_provenance").fetchone()[0] == 0


def test_direct_recall_rejects_observations_and_returns_only_local_authorized_text(db, monkeypatch):
    append(db)
    _, memory_id = accept(db, audience=(), visibility="shared")
    document = db.execute("SELECT document_id FROM memory_fact_index WHERE memory_id=?", (memory_id,)).fetchone()[0]
    db.execute("UPDATE memory_fact_index SET state='retained'")
    db.commit()

    class Client:
        def recall(self, **kwargs):
            return SimpleNamespace(
                results=[
                    SimpleNamespace(document_id=document, type="observation", text="UNVERIFIED OBSERVATION"),
                    SimpleNamespace(document_id=document, type="world", text="MALICIOUS ENRICHMENT"),
                ]
            )

        def close(self):
            pass

    monkeypatch.setattr(memory_backend, "hindsight_client", lambda **k: Client())
    results = memory_backend.recall_memory_results(
        db,
        "c",
        {"session_id": "s"},
        "silver key",
        "Bob",
        app_settings=make_test_settings(),
        remote_recall=identity_port(Client()),
    )
    assert len(results) == 1
    assert results[0].text == "The silver key is hidden in the tower."
