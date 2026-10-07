"""Explicit recall identity ports preserve durable purge and reindex authority."""

from types import SimpleNamespace

import pytest
from memory_runtime_test_support import isolated_memory_runtime as isolated_memory_runtime
from test_memory_completion_safety import session_db as session_db

from bridge import memory_backend
from bridge.memory_store import claim_jobs


def test_failed_public_purge_excludes_explicit_recall_and_preserves_cleanup_retry(session_db, monkeypatch):
    from functools import partial

    from hindsight_recall_test_support import identity_port
    from test_memory_native_backend import _FakeHindsight

    from bridge.memory import purge_hindsight_session

    settings, db, session = session_db
    client = _FakeHindsight()
    monkeypatch.setattr(memory_backend, "hindsight_client", lambda **kwargs: client)
    assert memory_backend.remember_fact(
        db, "chat", session, {"name": "Alice"}, "The key is blue", app_settings=settings
    )
    from bridge.memory_workers import run_memory_claim

    claim = claim_jobs(db, layers=("hindsight",))[0]
    assert run_memory_claim(db, claim, session, {"name": "Alice"}, app_settings=settings) == "complete"
    old_id = client.retained[-1]["document_id"]
    client.recall = lambda **kwargs: SimpleNamespace(
        results=[
            SimpleNamespace(document_id=document_id, text="The key is blue", type="world")
            for document_id in client.documents.documents
        ]
    )
    recall_results = partial(
        memory_backend.recall_memory_results, app_settings=settings, remote_recall=identity_port(client)
    )
    assert len(recall_results(db, "chat", session, "key", "Alice")) == 1
    original_list = client.documents.list_documents

    async def unavailable(**kwargs):
        raise RuntimeError("synthetic remote failure")

    monkeypatch.setattr(client.documents, "list_documents", unavailable)
    with pytest.raises(RuntimeError, match="cleanup failed"):
        purge_hindsight_session(db, "chat", "s1", app_settings=settings)
    assert recall_results(db, "chat", session, "key", "Alice") == []
    assert db.execute("SELECT document_id FROM hindsight_documents").fetchall() == [(old_id,)]
    assert db.execute("SELECT deleted FROM memory_retired_documents WHERE document_id=?", (old_id,)).fetchone() == (0,)

    # A new explicit request must not revive a retired identity, even before cleanup recovers.
    assert memory_backend.remember_fact(
        db, "chat", session, {"name": "Alice"}, "The key is blue", app_settings=settings
    )
    claim = claim_jobs(db, layers=("hindsight",))[0]
    assert run_memory_claim(db, claim, session, {"name": "Alice"}, app_settings=settings) == "complete"
    new_id = client.retained[-1]["document_id"]
    assert new_id != old_id
    recalled = recall_results(db, "chat", session, "key", "Alice")
    assert [item.document_id for item in recalled] == [new_id]
    assert memory_backend.cleanup_retired_memory_documents(db, "chat", "s1", app_settings=settings)
    assert old_id in client.documents.deleted
    assert new_id not in client.documents.deleted
    assert [item.document_id for item in recall_results(db, "chat", session, "key", "Alice")] == [new_id]
    monkeypatch.setattr(client.documents, "list_documents", original_list)
    assert purge_hindsight_session(db, "chat", "s1", app_settings=settings) > 0
    assert {old_id, new_id} <= set(client.documents.deleted)
    assert db.execute("SELECT count(*) FROM hindsight_documents").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM memory_retired_documents WHERE deleted=0").fetchone()[0] == 0
    assert recall_results(db, "chat", session, "key", "Alice") == []
