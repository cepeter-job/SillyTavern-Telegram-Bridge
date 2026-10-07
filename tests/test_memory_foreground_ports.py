"""Foreground ports carry identities while SQLite keeps all story authority."""

from dataclasses import replace
from functools import partial

import pytest
from application_test_setup import make_test_model_router
from hindsight_recall_test_support import Client, Gate, recall, runtime_for
from settings_test_support import make_test_settings
from test_story_memory_artifacts import service, write_artifacts
from test_story_memory_scope import accept, append, scope
from test_story_memory_scope import db as db

from bridge import memory_backend


def retained_fact(db):
    row = append(db)
    _, fact = accept(db, audience=(), visibility="shared")
    db.execute("UPDATE memory_fact_index SET state='retained'")
    db.commit()
    document = db.execute("SELECT document_id FROM memory_fact_index WHERE memory_id=?", (fact,)).fetchone()[0]
    return row, document


def test_missing_explicit_port_never_constructs_client_or_lazy_runtime(db, monkeypatch):
    retained_fact(db)
    monkeypatch.setattr(memory_backend, "hindsight_client", lambda **kwargs: pytest.fail("Lazy client construction"))
    assert (
        memory_backend.recall_memory_results(
            db, "c", {"session_id": "s"}, "silver key", "Mira", app_settings=make_test_settings()
        )
        == []
    )


@pytest.mark.parametrize("mutation", ["reset", "rewrite", "off", "delete"])
def test_foreground_port_revalidates_scope_and_memory_mode_after_return(db, mutation):
    from bridge.memory_store import purge_external_memory
    from bridge.metadata import set_meta

    row, document = retained_fact(db)
    captured = scope(db)

    def remote(**kwargs):
        assert kwargs == {
            "bank_id": memory_backend.hindsight_bank_id("c"),
            "session_id": "s",
            "query": "silver key",
            "max_tokens": 1600,
        }
        assert not db.in_transaction
        if mutation == "reset":
            purge_external_memory(db, "c", "s", purge_epoch=2)
        elif mutation == "rewrite":
            db.execute("UPDATE messages SET content='Replacement' WHERE id=?", (row,))
            db.commit()
        elif mutation == "off":
            set_meta(db, "memory_mode:c", "off")
        else:
            db.execute("DELETE FROM sessions WHERE chat_id='c' AND session_id='s'")
            db.commit()
        return ((document, "world"),)

    assert (
        memory_backend.recall_memory_results(
            db,
            "c",
            {"session_id": "s"},
            "silver key",
            "Mira",
            1600,
            app_settings=make_test_settings(),
            read_scope=captured,
            remote_recall=remote,
        )
        == []
    )


def test_explicit_port_only_rehydrates_current_locally_authorized_ids(db):
    _, document = retained_fact(db)
    results = memory_backend.recall_memory_results(
        db,
        "c",
        {"session_id": "s"},
        "silver key",
        "Mira",
        app_settings=make_test_settings(),
        remote_recall=lambda **kwargs: (
            (document, "observation"),
            ("foreign", "world"),
            (document, "world"),
            (document, "world"),
        ),
    )
    assert [(item.document_id, item.text, item.type) for item in results] == [
        (document, "The silver key is hidden in the tower.", "world")
    ]


@pytest.mark.parametrize("unavailable", ["not started", "timeout", "circuit", "saturation", "startup failure"])
def test_unavailable_semantic_recall_preserves_local_prompt_context(db, unavailable):
    from concurrent.futures import ThreadPoolExecutor

    row, _document = retained_fact(db)
    write_artifacts(db, row)
    baseline = service().prompt_context(db, "c", {"session_id": "s"}, {"name": "Bob"}, "silver key")
    queries = ("0", "1") if unavailable == "saturation" else ("silver key",)
    gates = {query: Gate(resistant=True) for query in queries}
    client = Client(lambda kwargs: gates[kwargs["query"]].wait())
    runtime = runtime_for(client, timeout_seconds=0.08 if unavailable in {"timeout", "circuit"} else 1)
    if unavailable == "startup failure":
        from bridge.hindsight_recall_runtime import HindsightRecallRuntime

        def broken(**kwargs):
            raise ImportError("synthetic optional SDK failure")

        runtime = HindsightRecallRuntime(base_url="http://127.0.0.1:8888", api_key=None, client_factory=broken)
    if unavailable != "not started":
        runtime.start()
    with ThreadPoolExecutor(max_workers=2) as callers:
        futures = []
        try:
            if unavailable == "circuit":
                assert recall(runtime) == ()
            if unavailable == "saturation":
                futures = [callers.submit(recall, runtime, query) for query in queries]
                # Each admitted waiter owns its release event. A queued callback
                # cannot prove another caller has entered the fake on this loop.
                assert all(gate.entered.wait(0.5) for gate in gates.values())
                assert sorted(call["query"] for call in client.calls) == ["0", "1"]
            memory = service(
                partial(
                    memory_backend.recall_scoped_memory, app_settings=make_test_settings(), remote_recall=runtime.recall
                )
            )
            context = memory.prompt_context(db, "c", {"session_id": "s"}, {"name": "Bob"}, "silver key")
            assert (context.episodic, context.summary, context.scene) == (
                baseline.episodic,
                baseline.summary,
                baseline.scene,
            )
            assert "silver key" in context.episodic and context.summary and context.scene
            assert context.recall == ""
            if unavailable == "saturation":
                assert sorted(call["query"] for call in client.calls) == ["0", "1"]
        finally:
            runtime.begin_shutdown()
            for gate in gates.values():
                gate.release()
            assert runtime.close(timeout=1)
            assert all(future.result(timeout=0.2) == () for future in futures)


def test_composition_creates_independent_inactive_runtimes_and_one_shared_port_per_service(db, monkeypatch):
    import bridge.main as main
    from bridge.hindsight_recall_runtime import HindsightRecallRuntime

    _, document = retained_fact(db)
    calls = []

    def remote(self, **kwargs):
        calls.append((self, kwargs))
        return ((document, "world"),)

    monkeypatch.setattr(HindsightRecallRuntime, "recall", remote)
    monkeypatch.setattr(HindsightRecallRuntime, "start", lambda self: pytest.fail("Composition started runtime"))
    first, second = [
        main._build_startup_services(make_test_settings(), model_router=make_test_model_router()) for _ in range(2)
    ]
    assert first.hindsight_recall is not second.hindsight_recall
    assert first.memory.search(db, "c", {"session_id": "s"}, "silver key", "Bob")
    assert first.memory.scoped_recall(db, scope(db, "Bob"), "silver key").text
    assert [runtime for runtime, _kwargs in calls] == [first.hindsight_recall, first.hindsight_recall]
    assert first.memory.queue_session_cleanup is not None


def test_invalid_optional_endpoint_does_not_break_composition(db):
    import bridge.main as main

    settings = replace(make_test_settings(), environ={"HINDSIGHT_API_URL": "http://external.example"})
    services = main._build_startup_services(settings, model_router=make_test_model_router())
    assert services.hindsight_recall is None
    assert services.memory.search(db, "c", {"session_id": "s"}, "silver key") == []


def test_legacy_broad_scope_preference_cannot_expand_explicit_recall_port(db):
    from bridge.metadata import set_meta

    append(db)
    set_meta(db, "memory_scope:c", "user")
    calls = []

    def remote(**kwargs):
        calls.append(kwargs)
        return ()

    assert memory_backend.memory_scope(db, "c") == "session"
    assert (
        memory_backend.recall_memory_results(
            db,
            "c",
            {"session_id": "s"},
            "old fact",
            "Test",
            app_settings=make_test_settings(),
            remote_recall=remote,
        )
        == []
    )
    assert len(calls) == 1 and calls[0]["session_id"] == "s"
