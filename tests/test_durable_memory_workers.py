"""Exercise recoverable workers with real synthetic databases; fake external I/O only."""

import json
from types import SimpleNamespace

import pytest
from application_test_setup import make_test_provider_port
from memory_runtime_test_support import isolated_memory_runtime as isolated_memory_runtime
from test_memory_completion_safety import session_db as session_db

from bridge import memory_backend
from bridge.memory_store import claim_jobs
from bridge.metadata import set_meta


@pytest.fixture(autouse=True)
def forbid_real_hindsight_client(monkeypatch):
    def forbidden(**kwargs):
        pytest.fail("Durable memory tests must explicitly fake the Hindsight client")

    monkeypatch.setattr(memory_backend, "hindsight_client", forbidden)


def add(db, text="A durable event"):
    db.execute(
        "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('chat','s1','user',?,1)", (text,)
    )
    db.commit()


def test_executor_rejection_releases_lease_without_completion(session_db):
    from bridge.memory_workers import dispatch_memory_backlog

    settings, db, _ = session_db
    add(db)
    services = SimpleNamespace(config=settings, background=SimpleNamespace(submit=lambda *a, **k: False))
    assert dispatch_memory_backlog(services, db) == 0
    assert db.execute("SELECT sum(completed_version),sum(lease_token<>'') FROM memory_jobs").fetchone() == (0, 0)
    assert db.execute("SELECT count(*) FROM memory_jobs WHERE last_error='executor_rejected'").fetchone()[0] > 0


def test_failed_retain_retries_same_document_and_only_success_advances(session_db, monkeypatch):
    from bridge.memory_workers import run_memory_claim

    settings, db, session = session_db
    add(db, "tail " * 10000)
    set_meta(db, "memory_mode:chat", "on")
    observed = []
    monkeypatch.setattr(memory_backend, "_retain_with_client", lambda *args, **kw: observed.append(args) or False)
    claim = claim_jobs(db, layers=("hindsight",))[0]
    assert run_memory_claim(db, claim, session, {"name": "Alice"}, app_settings=settings) == "retain_failed"
    assert db.execute("SELECT count(*) FROM memory_segments WHERE valid=1").fetchone()[0] == 0
    db.execute("UPDATE memory_jobs SET next_attempt_at=0")
    db.commit()
    monkeypatch.setattr(memory_backend, "_retain_with_client", lambda *args, **kw: observed.append(args) or True)
    claim = claim_jobs(db, layers=("hindsight",))[0]
    assert run_memory_claim(db, claim, session, {"name": "Alice"}, app_settings=settings) == "complete"
    assert observed[0][2] == observed[1][2]
    assert sum(len(args[4]) for args in observed[1:]) == 50000
    assert db.execute("SELECT completed_version FROM memory_jobs WHERE layer='hindsight'").fetchone()[0] == 1


def test_memory_off_and_resume_durable_source(session_db, monkeypatch):
    from bridge.memory_workers import run_memory_claim

    settings, db, session = session_db
    add(db)
    set_meta(db, "memory_mode:chat", "off")
    monkeypatch.setattr(memory_backend, "_retain_with_client", lambda *a, **k: pytest.fail("Disabled network"))
    claim = claim_jobs(db, layers=("hindsight",))[0]
    assert run_memory_claim(db, claim, session, {"name": "Alice"}, app_settings=settings) == "disabled"
    set_meta(db, "memory_mode:chat", "on")
    db.execute("UPDATE memory_jobs SET next_attempt_at=0")
    db.commit()
    monkeypatch.setattr(memory_backend, "_retain_with_client", lambda *a, **k: True)
    claim = claim_jobs(db, layers=("hindsight",))[0]
    assert run_memory_claim(db, claim, session, {"name": "Alice"}, app_settings=settings) == "complete"


def test_purge_during_retain_cannot_commit_source(session_db, monkeypatch):
    from bridge.memory_store import invalidate_memory
    from bridge.memory_workers import run_memory_claim

    settings, db, session = session_db
    add(db)
    set_meta(db, "memory_mode:chat", "on")

    def retain(*args, **kwargs):
        assert not db.in_transaction
        invalidate_memory(db, "chat", "s1", purge_epoch=1)
        return True

    monkeypatch.setattr(memory_backend, "_retain_with_client", retain)
    claim = claim_jobs(db, layers=("hindsight",))[0]
    assert run_memory_claim(db, claim, session, {"name": "Alice"}, app_settings=settings) == "stale_source"
    assert db.execute("SELECT count(*) FROM memory_segments WHERE valid=1").fetchone()[0] == 0
    assert db.execute("SELECT completed_version FROM memory_jobs WHERE layer='hindsight'").fetchone()[0] == 0


def test_empty_extraction_succeeds_but_stale_snapshot_is_distinct(session_db):
    from bridge.episodic_extraction import extract_episodic_memories_result

    settings, db, session = session_db
    add(db)
    kwargs = dict(source_text="event", source_start_rowid=1, source_end_rowid=1, app_settings=settings)
    result = extract_episodic_memories_result(
        db, "chat", session, provider_port=make_test_provider_port(generate_backend=lambda *a, **k: "[]"), **kwargs
    )
    assert result.status == "complete" and result.inserted == 0
    result = extract_episodic_memories_result(
        db,
        "chat",
        session,
        expected_source=(0, 0, 0),
        provider_port=make_test_provider_port(generate_backend=lambda *a, **k: pytest.fail("stale call")),
        **kwargs,
    )
    assert result.status == "stale"


def test_summary_success_episode_failure_is_still_pending(session_db, monkeypatch):
    from bridge.memory_workers import run_memory_claim

    settings, db, session = session_db
    add(db)
    summary = claim_jobs(db, layers=("summary",))[0]
    assert (
        run_memory_claim(
            db,
            summary,
            session,
            {"name": "Alice"},
            provider_port=make_test_provider_port(
                generate_backend=lambda *a, **k: '{"blocks":[{"text":"summary","visibility":"shared","known_by":[]}]}'
            ),
            app_settings=settings,
        )
        == "complete"
    )
    episodes = claim_jobs(db, layers=("episodes",))[0]
    result = run_memory_claim(
        db,
        episodes,
        session,
        {"name": "Alice"},
        provider_port=make_test_provider_port(generate_backend=lambda *a, **k: "invalid"),
        app_settings=settings,
    )
    assert result == "work_failed"
    assert db.execute("SELECT completed_version FROM memory_jobs WHERE layer='episodes'").fetchone()[0] == 0
    db.execute("UPDATE memory_jobs SET next_attempt_at=0")
    db.commit()
    episodes = claim_jobs(db, layers=("episodes",))[0]
    assert (
        run_memory_claim(
            db,
            episodes,
            session,
            {"name": "Alice"},
            provider_port=make_test_provider_port(generate_backend=lambda *a, **k: "[]"),
            app_settings=settings,
        )
        == "complete"
    )
    assert db.execute("SELECT count(*) FROM memory_segments WHERE layer='episodes' AND valid=1").fetchone()[0] == 1


def test_branch_seeding_retains_all_target_rows_and_full_text(session_db, monkeypatch):
    settings, db, session = session_db
    set_meta(db, "memory_mode:chat", "on")
    for n in range(110):
        add(db, f"target {n} " + ("z" * 20000 if n == 0 else ""))
    observed = []
    monkeypatch.setattr(memory_backend, "_retain_with_client", lambda *a, **k: observed.append(a) or True)
    for _ in range(20):
        status = memory_backend.seed_session_memory_now(db, "chat", session, app_settings=settings)
        if status == "ready":
            break
    assert status == "ready"
    assert all(any(f"target {n} " in args[4] for args in observed) for n in range(110))
    assert sum(args[4].count("z") for args in observed) == 20000
    assert all(args[1] == "s1" for args in observed)


def test_rewrite_during_external_retain_is_stale_and_does_not_ack(session_db, monkeypatch):
    from bridge.memory_workers import run_memory_claim

    settings, db, session = session_db
    add(db)

    def retain(*args, **kwargs):
        db.execute("UPDATE messages SET content='revised'")
        db.commit()
        return True

    monkeypatch.setattr(memory_backend, "_retain_with_client", retain)
    claim = claim_jobs(db, layers=("hindsight",))[0]
    assert run_memory_claim(db, claim, session, {"name": "Alice"}, app_settings=settings) == "stale_source"
    assert db.execute("SELECT completed_version FROM memory_jobs WHERE layer='hindsight'").fetchone()[0] == 0


def test_durable_summary_runs_one_bounded_call_and_resumes_prefix(session_db):
    from bridge.memory import generate_session_summary_result

    settings, db, session = session_db
    for n in range(30):
        add(db, f"row {n} " + "x" * 2400)
    calls = []
    provider = make_test_provider_port(
        generate_backend=lambda *args, **kw: (
            calls.append(args) or '{"blocks":[{"text":"Summary","visibility":"shared","known_by":[]}]}'
        )
    )
    result = generate_session_summary_result(
        db,
        "chat",
        session,
        force=True,
        durable=True,
        max_segments=1,
        provider_port=provider,
        app_settings=settings,
    )
    assert len(calls) == 1 and not result.complete
    covered = result.covered_until_rowid
    db.execute("UPDATE memory_jobs SET next_attempt_at=0 WHERE layer='summary'")
    db.commit()
    result = generate_session_summary_result(
        db,
        "chat",
        session,
        force=True,
        durable=True,
        max_segments=1,
        provider_port=provider,
        app_settings=settings,
    )
    assert len(calls) == 2 and not result.complete
    assert result.covered_until_rowid == 2 and covered == 1
    for expected in range(3, 31):
        db.execute("UPDATE memory_jobs SET next_attempt_at=0 WHERE layer='summary'")
        db.commit()
        result = generate_session_summary_result(
            db,
            "chat",
            session,
            force=True,
            durable=True,
            max_segments=1,
            provider_port=provider,
            app_settings=settings,
        )
        assert result.covered_until_rowid == expected
        assert result.complete is (expected == 30)
    result = generate_session_summary_result(
        db,
        "chat",
        session,
        force=True,
        durable=True,
        max_segments=1,
        provider_port=provider,
        app_settings=settings,
    )
    assert result.complete and len(calls) == 30


def test_claim_captured_before_purge_cannot_retain_rebuilt_source(session_db, monkeypatch):
    from bridge.memory_store import invalidate_memory
    from bridge.memory_workers import run_memory_claim

    settings, db, session = session_db
    add(db)
    claim = claim_jobs(db, layers=("hindsight",))[0]
    invalidate_memory(db, "chat", "s1", purge_epoch=1)
    retained = []
    monkeypatch.setattr(memory_backend, "_retain_with_client", lambda *a, **k: retained.append(a) or True)
    assert run_memory_claim(db, claim, session, {"name": "Alice"}, app_settings=settings) == "stale_source"
    assert retained == []


def test_external_purge_keeps_native_progress_and_only_retains_new_turns(session_db, monkeypatch):
    from bridge.memory import purge_hindsight_session
    from bridge.memory_workers import run_memory_claim

    settings, db, session = session_db
    add(db, "pre-purge event")
    db.execute("UPDATE memory_layer_state SET covered_id=1 WHERE layer<>'hindsight'")
    db.commit()
    from test_memory_native_backend import _FakeHindsight

    client = _FakeHindsight()
    monkeypatch.setattr(memory_backend, "hindsight_client", lambda **kwargs: client)
    retained = []
    monkeypatch.setattr(memory_backend, "_retain_with_client", lambda *a, **k: retained.append(a) or True)
    purge_hindsight_session(db, "chat", "s1", app_settings=settings)
    assert claim_jobs(db, layers=("hindsight",)) == []
    assert {row[0] for row in db.execute("SELECT covered_id FROM memory_layer_state WHERE layer<>'hindsight'")} == {1}
    add(db, "post-purge event")
    claim = claim_jobs(db, layers=("hindsight",))[0]
    assert run_memory_claim(db, claim, session, {"name": "Alice"}, app_settings=settings) == "complete"
    assert [a[4] for a in retained] == ["post-purge event"]


def test_curator_keeps_native_facts_without_rewriting_retired_fixed_document(session_db, monkeypatch):
    from bridge.memory_curator import curate_memory_now, get_curated_memory_state

    settings, db, session = session_db
    add(db)
    observed = []
    monkeypatch.setattr("bridge.memory_curator._retain_with_client", lambda *a, **k: observed.append(a) or True)
    provider = make_test_provider_port(
        generate_backend=lambda *a, **k: (
            '{"memories":[{"key":"arrival","text":"Alice arrived","kind":"event","confidence":1}]}'
        )
    )
    result = curate_memory_now(db, "", "chat", session, "Alice", provider_port=provider, app_settings=settings)
    assert result[0]["text"] == "Alice arrived"
    assert get_curated_memory_state(db, "chat", "s1")[1] == 1
    assert observed == []


def test_external_purge_preserves_native_curator_items(session_db, monkeypatch):
    from bridge.memory import purge_hindsight_session
    from bridge.memory_curator import get_curated_memory_state

    settings, db, _ = session_db
    add(db)
    set_meta(
        db,
        "memory_curator:chat:s1",
        '{"items":[{"key":"arrival","text":"Alice arrived","kind":"event"}],"through_rowid":1}',
    )
    from test_memory_native_backend import _FakeHindsight

    client = _FakeHindsight()
    monkeypatch.setattr(memory_backend, "hindsight_client", lambda **kwargs: client)
    purge_hindsight_session(db, "chat", "s1", app_settings=settings)
    assert get_curated_memory_state(db, "chat", "s1")[0][0]["text"] == "Alice arrived"


def test_branch_seed_call_has_bounded_external_work(session_db, monkeypatch):
    settings, db, session = session_db
    for n in range(12):
        add(db, f"row {n}")
    observed = []
    monkeypatch.setattr(memory_backend, "_retain_with_client", lambda *a, **k: observed.append(a) or True)
    assert memory_backend.seed_session_memory_now(db, "chat", session, app_settings=settings) == "degraded"
    assert len(observed) == 8
    assert memory_backend.seed_session_memory_now(db, "chat", session, app_settings=settings) == "ready"
    assert len(observed) == 12


def test_direct_sql_npc_rewrite_recovers_fixed_suffix_and_preserves_prior_field(session_db):
    from bridge.memory_workers import run_memory_claim
    from bridge.npc_repository import load_npc_fields, set_npc_extraction_coverage
    from bridge.npc_service import NpcService
    from bridge.npc_types import NpcExtractionGroup, NpcOperation
    from bridge.sqlite_store import write_transaction

    settings, db, session = session_db
    add(db, "Maya speaks softly")
    add(db, "Maya has blue hair")
    npc = NpcService()
    for row_id, field, value in [(1, "voice", "soft"), (2, "appearance", "blue hair")]:
        npc.apply_group(
            db,
            "chat",
            "s1",
            NpcExtractionGroup("Maya", (), (NpcOperation(field, "set", value, "fixed", "shared", ()),)),
            source_rowid=row_id,
            primary_name="Alice",
            user_name="User",
        )
    with write_transaction(db):
        set_npc_extraction_coverage(db, "chat", "s1", 2, 3)
    db.execute("UPDATE messages SET content='Maya has red hair' WHERE id=2")
    db.commit()
    claim = claim_jobs(db, layers=("npc",))[0]
    supplied = []

    def extract_source(_key, _model, messages, **kwargs):
        text = messages[-1]["content"].split("\n\nCanonical source part:\n", 1)[1]
        supplied.append(text)
        field, value = ("voice", "soft") if text == "Maya speaks softly" else ("appearance", "red hair")
        return json.dumps(
            {
                "npcs": [
                    {
                        "name": "Maya",
                        "aliases": [],
                        "operations": [
                            {
                                "field": field,
                                "op": "set",
                                "value": value,
                                "mode": "fixed",
                                "visibility": "shared",
                                "known_by": [],
                            }
                        ],
                    }
                ]
            }
        )

    provider = make_test_provider_port(generate_backend=extract_source)
    assert (
        run_memory_claim(db, claim, session, {"name": "Alice"}, provider_port=provider, app_settings=settings)
        == "complete"
    )
    entity = npc.list_npcs(db, "chat", "s1")[0]
    fields = load_npc_fields(db, entity.npc_id)
    assert fields["voice"].value == "soft"
    assert fields["appearance"].value == "red hair"
    # Without a persisted complete-prefix checkpoint, replay from the source floor.
    assert supplied == ["Maya speaks softly", "Maya has red hair"]


def test_durable_summary_coverage_uses_canonical_ids_with_backdated_append(session_db):
    from bridge.memory_workers import run_memory_claim

    settings, db, session = session_db
    add(db, "first")
    add(db, "imported later")
    db.execute("UPDATE messages SET created_at=100 WHERE id=1")
    db.commit()
    claim = claim_jobs(db, layers=("summary",))[0]
    result = run_memory_claim(
        db,
        claim,
        session,
        {"name": "Alice"},
        provider_port=make_test_provider_port(
            generate_backend=lambda *a, **k: '{"blocks":[{"text":"Summary","visibility":"shared","known_by":[]}]}'
        ),
        app_settings=settings,
    )
    assert result == "complete"
    assert db.execute("SELECT covered_until_rowid FROM session_summaries").fetchone()[0] == 2


def test_failed_public_purge_excludes_explicit_recall_and_preserves_cleanup_retry(session_db, monkeypatch):
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
    assert len(memory_backend.recall_memory_results(db, "chat", session, "key", "Alice", app_settings=settings)) == 1
    original_list = client.documents.list_documents

    async def unavailable(**kwargs):
        raise RuntimeError("synthetic remote failure")

    monkeypatch.setattr(client.documents, "list_documents", unavailable)
    with pytest.raises(RuntimeError, match="cleanup failed"):
        purge_hindsight_session(db, "chat", "s1", app_settings=settings)
    assert memory_backend.recall_memory_results(db, "chat", session, "key", "Alice", app_settings=settings) == []
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
    recalled = memory_backend.recall_memory_results(db, "chat", session, "key", "Alice", app_settings=settings)
    assert [item.document_id for item in recalled] == [new_id]
    assert memory_backend.cleanup_retired_memory_documents(db, "chat", "s1", app_settings=settings)
    assert old_id in client.documents.deleted
    assert new_id not in client.documents.deleted
    assert [
        item.document_id
        for item in memory_backend.recall_memory_results(db, "chat", session, "key", "Alice", app_settings=settings)
    ] == [new_id]
    monkeypatch.setattr(client.documents, "list_documents", original_list)
    assert purge_hindsight_session(db, "chat", "s1", app_settings=settings) > 0
    assert {old_id, new_id} <= set(client.documents.deleted)
    assert db.execute("SELECT count(*) FROM hindsight_documents").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM memory_retired_documents WHERE deleted=0").fetchone()[0] == 0
    assert memory_backend.recall_memory_results(db, "chat", session, "key", "Alice", app_settings=settings) == []


@pytest.mark.parametrize("replace_token", [False, True])
def test_delayed_dispatch_stale_closure_releases_only_its_own_lease(session_db, replace_token):
    from bridge.memory_workers import dispatch_memory_backlog
    from bridge.sqlite_store import db_connect

    settings, db, _ = session_db
    add(db)
    scheduled = []

    def accept(_kind, worker, *args):
        scheduled.append((worker, args))
        return True

    services = SimpleNamespace(
        config=settings,
        background=SimpleNamespace(submit=accept),
        db_factory=lambda: db_connect(app_settings=settings),
        session=SimpleNamespace(load=lambda *args: pytest.fail("Stale closure must not reconstruct a session")),
    )
    assert dispatch_memory_backlog(services, db) == 1
    worker, args = scheduled[0]
    abandoned = args[-1]
    db.execute("UPDATE messages SET content='Rewritten before accepted dispatch starts'")
    db.commit()
    replacement = None
    if replace_token:
        db.execute("UPDATE memory_jobs SET lease_deadline=0 WHERE layer=?", (abandoned.layer,))
        db.commit()
        replacement = claim_jobs(db, layers=(abandoned.layer,))[0]
        assert replacement.token != abandoned.token
        replacement_state = db.execute("SELECT * FROM memory_jobs WHERE layer=?", (abandoned.layer,)).fetchone()

    worker(*args)
    row = db.execute(
        "SELECT lease_token,completed_version,dirty_version,last_error FROM memory_jobs WHERE layer=?",
        (abandoned.layer,),
    ).fetchone()
    assert row[1] == 0
    assert row[2] > abandoned.version
    assert db.execute("SELECT count(*) FROM memory_jobs WHERE dirty_version>completed_version").fetchone()[0] == 6
    if replacement:
        assert db.execute("SELECT * FROM memory_jobs WHERE layer=?", (abandoned.layer,)).fetchone() == replacement_state
        assert row[0] == replacement.token
        assert row[3] == ""
        assert claim_jobs(db) == []
    else:
        assert row[0] == ""
        assert row[3] == "stale_source"
        assert dispatch_memory_backlog(services, db) == 1
        assert scheduled[-1][1][-1].layer != abandoned.layer


def test_durable_worker_module_fails_before_real_client_construction(session_db):
    settings, _, _ = session_db
    with pytest.raises(pytest.fail.Exception, match="must explicitly fake"):
        memory_backend.hindsight_client(app_settings=settings)
