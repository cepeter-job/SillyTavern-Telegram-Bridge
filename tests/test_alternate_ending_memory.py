"""Branch readiness commits local work; native summaries are indexed later."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from application_test_setup import make_test_provider_port
from test_alternate_ending import branch, close_story
from test_durable_memory_workers import add
from test_memory_completion_safety import session_db as session_db

from bridge.alternate_ending_memory import seed_alternate_ending_memory
from bridge.memory_contracts import MemoryFact
from bridge.memory_fact_store import accept_source_facts, remember_local_fact
from bridge.memory_store import next_source_segment
from bridge.meta_repository import store_meta_value
from bridge.sqlite_store import write_transaction


@pytest.mark.parametrize("mode", ["off", "on"])
def test_branch_is_locally_ready_without_a_remote_client(session_db, monkeypatch, mode):
    from bridge import memory_backend

    config, db, _ = session_db
    cp = close_story(session_db)
    original = db.execute("SELECT * FROM messages WHERE session_id='s1'").fetchall()
    with write_transaction(db):
        store_meta_value(db, "memory_mode:chat", mode)
    monkeypatch.setattr(memory_backend, "hindsight_client", lambda **k: pytest.fail("Branch constructed a client"))
    result = branch(
        session_db,
        cp,
        seed=lambda db, chat, session: seed_alternate_ending_memory(db, chat, session, app_settings=config),
    )
    assert result.applied and result.memory_status == "ready"
    assert db.execute("SELECT * FROM messages WHERE session_id='s1'").fetchall() == original
    assert db.execute("SELECT count(*) FROM memory_segments WHERE layer='hindsight'").fetchone() == (0,)
    assert seed_alternate_ending_memory(db, "chat", result.session, app_settings=config) == "ready"


def test_restored_target_native_facts_have_fresh_ids_and_background_indexing(session_db, monkeypatch):
    from bridge import memory_backend
    from bridge.memory_store import claim_jobs
    from bridge.memory_workers import run_memory_claim

    config, db, _ = session_db
    cp = close_story(
        session_db,
        seed=lambda db, row: accept_source_facts(
            db,
            next_source_segment(db, "chat", "s1", "episodes"),
            [MemoryFact("fact", 0.9, "The silver key opens the tower.", "shared", ())],
        ),
    )
    origin_id = db.execute("SELECT document_id FROM memory_fact_index WHERE session_id='s1'").fetchone()[0]
    target = branch(session_db, cp).session
    with write_transaction(db):
        store_meta_value(db, "memory_mode:chat", "on")
    observed = []

    @contextmanager
    def client_scope(**kwargs):
        yield SimpleNamespace(retain=lambda **kw: observed.append(kw))

    monkeypatch.setattr(memory_backend, "hindsight_client_scope", client_scope)
    before = db.execute(
        "SELECT layer,dirty_version,completed_version FROM memory_jobs WHERE session_id=? ORDER BY layer",
        (target["session_id"],),
    ).fetchall()
    assert seed_alternate_ending_memory(db, "chat", target, app_settings=config) == "ready"
    assert seed_alternate_ending_memory(db, "chat", target, app_settings=config) == "ready"
    assert observed == []
    assert (
        db.execute(
            "SELECT layer,dirty_version,completed_version FROM memory_jobs WHERE session_id=? ORDER BY layer",
            (target["session_id"],),
        ).fetchall()
        == before
    )
    document, state = db.execute(
        "SELECT document_id,state FROM memory_fact_index WHERE session_id=?", (target["session_id"],)
    ).fetchone()
    assert document != origin_id and state == "pending"
    claim = claim_jobs(db, layers=("hindsight",), chat_id="chat", session_id=target["session_id"])[0]
    assert run_memory_claim(db, claim, target, {"name": "Alice"}, app_settings=config) == "complete"
    assert len(observed) == 1
    assert observed[0]["document_id"] == document
    assert observed[0]["content"] == "The silver key opens the tower."
    assert "native-fact" in observed[0]["tags"]
    assert "session:" + target["session_id"] in observed[0]["tags"]
    assert "session:s1" not in observed[0]["tags"]
    assert not db.in_transaction


def test_seed_initializes_missing_local_jobs_and_repairs_pending_native_work_idempotently(session_db, monkeypatch):
    from bridge import memory_backend

    config, db, session = session_db
    remember_local_fact(db, "chat", "s1", "Alice", "A local assertion.")
    db.execute("UPDATE memory_jobs SET completed_version=dirty_version")
    db.commit()
    monkeypatch.setattr(memory_backend, "hindsight_client", lambda **k: pytest.fail("Local readiness client"))
    assert seed_alternate_ending_memory(db, "chat", session, app_settings=config) == "ready"
    jobs = db.execute("SELECT layer,dirty_version,completed_version FROM memory_jobs ORDER BY layer").fetchall()
    assert {row[0] for row in jobs} == {"episodes", "summary", "scene", "npc", "curator", "hindsight"}
    assert next(row[1] > row[2] for row in jobs if row[0] == "hindsight")
    assert seed_alternate_ending_memory(db, "chat", session, app_settings=config) == "ready"
    assert db.execute("SELECT layer,dirty_version,completed_version FROM memory_jobs ORDER BY layer").fetchall() == jobs


def test_seed_rejects_deleted_target_and_caller_transaction(session_db):
    config, db, session = session_db
    with write_transaction(db):
        store_meta_value(db, "transaction-proof", "pending")
        with pytest.raises(ValueError, match="transaction"):
            seed_alternate_ending_memory(db, "chat", session, app_settings=config)
        assert db.in_transaction
    db.execute("DELETE FROM sessions WHERE chat_id='chat' AND session_id='s1'")
    db.commit()
    with pytest.raises(ValueError, match=r"deleted|exists"):
        seed_alternate_ending_memory(db, "chat", session, app_settings=config)
    assert db.execute("SELECT count(*) FROM sessions").fetchone() == (0,)


def test_target_deleted_during_readiness_cannot_complete_operation(session_db):
    from bridge.alternate_ending_memory import seed_local_session_memory

    _, db, _ = session_db
    cp = close_story(session_db)

    def deleted_after_ready(db, chat_id, target):
        assert seed_local_session_memory(db, chat_id, target) == "ready"
        db.execute("DELETE FROM sessions WHERE chat_id=? AND session_id=?", (chat_id, target["session_id"]))
        db.commit()
        return "ready"

    with pytest.raises(ValueError, match=r"deleted|target"):
        branch(session_db, cp, seed=deleted_after_ready)
    assert db.execute("SELECT state FROM operations WHERE kind='alternate_ending'").fetchone() != ("applied",)


def test_branch_readiness_enqueues_complete_local_extraction_of_all_rows(session_db, monkeypatch):
    from bridge import memory_backend
    from bridge.memory_store import claim_jobs
    from bridge.memory_workers import run_memory_claim

    settings, db, session = session_db
    for n in range(110):
        add(db, f"target {n} " + ("z" * 20000 if n == 0 else ""))
    remote, extracted = [], []
    monkeypatch.setattr(memory_backend, "_retain_with_client", lambda *a, **k: remote.append(a) or True)
    assert seed_alternate_ending_memory(db, "chat", session, app_settings=settings) == "ready"
    assert remote == []
    provider = make_test_provider_port(
        generate_backend=lambda _key, _model, messages, **k: (
            extracted.append(messages[-1]["content"])
            or '{"memories":[],"no_memory_reason":"Only synthetic transient text was present."}'
        )
    )
    for _ in range(20):
        claims = claim_jobs(db, layers=("episodes",))
        if not claims:
            break
        assert run_memory_claim(
            db, claims[0], session, {"name": "Alice"}, provider_port=provider, app_settings=settings
        ) in {"complete", "deferred"}
    assert len(extracted) == 111
    assert all(any(f"target {n} " in text for text in extracted) for n in range(110))
    assert sum(text.count("z") for text in extracted) == 20000
    assert next_source_segment(db, "chat", "s1", "episodes") is None
    assert remote == []


def test_local_seed_defers_twelve_native_facts_to_bounded_background_work(session_db, monkeypatch):
    from bridge import memory_backend
    from bridge.memory_store import claim_jobs
    from bridge.memory_workers import run_memory_claim

    settings, db, session = session_db
    for n in range(12):
        remember_local_fact(db, "chat", "s1", "Alice", f"Accepted fact {n}.")
    observed = []
    monkeypatch.setattr(memory_backend, "_retain_with_client", lambda *a, **k: observed.append(a) or True)
    assert seed_alternate_ending_memory(db, "chat", session, app_settings=settings) == "ready"
    assert observed == []
    claim = claim_jobs(db, layers=("hindsight",))[0]
    assert run_memory_claim(db, claim, session, {"name": "Alice"}, app_settings=settings) == "deferred"
    assert len(observed) == 8
    claim = claim_jobs(db, layers=("hindsight",))[0]
    assert run_memory_claim(db, claim, session, {"name": "Alice"}, app_settings=settings) == "complete"
    assert len(observed) == 12 and all(a[6] == "native_fact" for a in observed)
