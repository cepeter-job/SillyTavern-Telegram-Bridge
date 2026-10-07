"""Operation timing observes real boundaries without forwarding metadata or content."""

import logging
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from threading import Barrier

import pytest
from application_test_setup import (
    ensure_application_extensions,
    make_test_group_service,
    make_test_memory_service,
    make_test_npc_service,
    make_test_persona_service,
    make_test_rag_service,
)

from bridge import performance
from bridge.card_content import card_fields
from bridge.conversation_lifecycle import mark_started
from bridge.provider_port import ProviderPort
from bridge.scheduler_safety import DurableWorkerGuard
from bridge.schema import initialize_database_schema
from bridge.settings import load_app_settings

ensure_application_extensions()
from bridge.message_commands import generate_and_store_reply


def settings(tmp_path, enabled=True):
    return load_app_settings({"SILLYTAVERN_PERF_LOG": "1" if enabled else "0"}, home=tmp_path)


def test_history_duration_includes_row_materialization(tmp_path, monkeypatch, caplog):
    # Removing fetchall from the timed boundary loses all three row-factory delays.
    config = settings(tmp_path)
    clock = [0.0]
    monkeypatch.setattr(performance.time, "perf_counter", lambda: clock[0])
    with closing(sqlite3.connect(tmp_path / "history.sqlite")) as db:
        initialize_database_schema(db)
        db.execute(
            "INSERT INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,world_file,"
            "created_at,updated_at) "
            "VALUES('c','s','Story','','m','','',1,1)"
        )
        db.executemany(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('c','s','user',?,?)",
            [("synthetic one", 1), ("synthetic two", 2), ("synthetic three", 3)],
        )
        db.commit()
        assert mark_started(db, "c", "s", 0)

        def materialize(cursor, row):
            if [column[0] for column in cursor.description] == ["role", "content"]:
                clock[0] += 0.02
            return row

        db.row_factory = materialize

        class StopAfterHistory(Exception):
            pass

        class RetrievalBoundary:
            def bundle(self, *_args):
                raise StopAfterHistory

        with caplog.at_level(logging.INFO), pytest.raises(StopAfterHistory):
            generate_and_store_reply(
                db,
                "synthetic-token",
                "synthetic-key",
                {"name": "Fixture"},
                "c",
                "synthetic input",
                {"session_id": "s"},
                "s",
                "m",
                None,
                "",
                None,
                None,
                group_service=None,
                provider_port=ProviderPort(lambda *_a, **_k: "unused"),
                memory_service=None,
                npc_service=None,
                persona_service=None,
                app_settings=config,
                rag_service=RetrievalBoundary(),
            )
        message = next(record.getMessage() for record in caplog.records if "span=history_load " in record.getMessage())
        assert float(re.search(r"duration_ms=([\d.]+)", message)[1]) == pytest.approx(60)


@pytest.mark.parametrize("attempts, expected_wait", [(0, 4000), (1, 2000)])
def test_durable_worker_links_queue_and_boundaries_without_extra_arguments(
    tmp_path, monkeypatch, caplog, attempts, expected_wait
):
    # Losing the scope at dispatch or forwarding it to a callable breaks this contract.
    config = settings(tmp_path)
    monkeypatch.setattr(performance.time, "time", lambda: 12.0)
    db = sqlite3.connect(tmp_path / "jobs.sqlite")
    try:
        initialize_database_schema(db)
        db.execute(
            "INSERT INTO jobs(update_id,chat_id,session_id,kind,payload_json,state,created_at,updated_at) "
            "VALUES(7,'private-chat','s','generation','{}','queued',8,8)"
        )
        db.execute("UPDATE jobs SET attempts=?,updated_at=10", (attempts,))
        db.commit()
        job_id = db.execute("SELECT job_id FROM jobs WHERE update_id=7").fetchone()[0]

        def boundary(value, *, option):
            assert (value, option) == ("private story text", "synthetic credential")
            return 42

        def worker(value):
            for name in ("context_assembly", "provider_stream", "reply_delivery"):
                assert (
                    performance.timed_call(name, boundary, value, option="synthetic credential", app_settings=config)
                    == 42
                )
            return "finished"

        guard = DurableWorkerGuard(lambda *_a, **_k: None, app_settings=config)
        guarded = guard.prepare(db, job_id, worker)
        with caplog.at_level(logging.INFO):
            assert guarded("private story text") == "finished"
        messages = [record.getMessage() for record in caplog.records if record.getMessage().startswith("perf ")]
        assert any(f"span=queue_wait duration_ms={expected_wait:.3f}" in message for message in messages)
        for name in ("queue_wait", "context_assembly", "provider_stream", "reply_delivery"):
            assert any(f"span={name} " in message and f"operation_id=job-{job_id}" in message for message in messages)
        assert not any(
            secret in "\n".join(messages) for secret in ("private story text", "synthetic credential", "private-chat")
        )
    finally:
        db.close()


def test_concurrent_operation_scopes_are_isolated_and_restored(tmp_path, caplog):
    # A process-global identifier would mix the operations at the barrier.
    config = settings(tmp_path)
    barrier = Barrier(2)

    def operation(job_id):
        with performance.operation_scope(job_id, app_settings=config):
            barrier.wait(timeout=5)
            performance.timed_call(f"boundary_{job_id}", lambda: None, app_settings=config)
            with performance.operation_scope(job_id + 10, app_settings=config):
                performance.timed_call(f"nested_{job_id}", lambda: None, app_settings=config)
            performance.timed_call(f"restored_{job_id}", lambda: None, app_settings=config)

    with caplog.at_level(logging.INFO), ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(operation, (101, 102)))
    messages = [record.getMessage() for record in caplog.records]
    for job_id in (101, 102):
        for name, expected in (("boundary", job_id), ("nested", job_id + 10), ("restored", job_id)):
            assert any(
                f"span={name}_{job_id} " in message and f"operation_id=job-{expected}" in message
                for message in messages
            )
    with caplog.at_level(logging.INFO):
        performance.timed_call("outside", lambda: None, app_settings=config)
    assert "operation_id=" not in caplog.records[-1].getMessage()


def test_disabled_timing_does_not_read_clocks_or_format_fields(tmp_path, monkeypatch):
    # Disabled instrumentation must not perform timing, ID generation, or log formatting.
    config = settings(tmp_path, enabled=False)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("disabled telemetry performed work")

    class UnsafeField:
        def __str__(self):
            forbidden()

    monkeypatch.setattr(performance.time, "perf_counter", forbidden)
    monkeypatch.setattr(performance.time, "time", forbidden)
    monkeypatch.setattr(performance.logging, "info", forbidden)
    monkeypatch.setattr(performance.uuid, "uuid4", forbidden)
    with performance.operation_scope(7, app_settings=config, queued_at=1):
        with performance.perf_span("phase", app_settings=config, prompt=UnsafeField()):
            assert performance.timed_call("call", lambda: 42, app_settings=config) == 42


def test_span_fields_cannot_log_arbitrary_content(tmp_path, caplog):
    # Permitting arbitrary string fields would expose prompts and credentials.
    with caplog.at_level(logging.INFO), performance.operation_scope(7, app_settings=settings(tmp_path)):
        with performance.perf_span(
            "phase",
            app_settings=settings(tmp_path),
            rows=3,
            prompt="private story text",
            api_key="synthetic credential",
        ):
            pass
    message = caplog.records[-1].getMessage()
    assert "rows=3" in message and "operation_id=job-7" in message
    assert "private story text" not in message and "synthetic credential" not in message


def test_actual_reply_reports_context_provider_and_delivery_boundaries(tmp_path, monkeypatch, caplog):
    # Removing a use-case span must fail even if the generic timing utility still works.
    import bridge.telegram as telegram

    config = settings(tmp_path)
    clock = [0.0]
    monkeypatch.setattr(performance.time, "perf_counter", lambda: clock[0])

    def scoped_memory(*_args, **_kwargs):
        clock[0] += 0.03
        return None

    def provider(
        api_key,
        model,
        messages,
        *,
        session_id,
        settings,
        stream_callback,
        cancel_event,
        force_non_stream,
        request_timeout,
        context_observer,
    ):
        assert api_key == "synthetic credential" and model == "m"
        assert messages[-1]["content"] == "private story text"
        clock[0] += 0.05
        return "A synthetic reply."

    def transport(token, method, payload):
        assert token == "synthetic token"
        if method == "sendMessage":
            clock[0] += 0.02
            return {"message_id": 41}
        assert method == "sendChatAction"
        return {}

    monkeypatch.setattr(telegram, "telegram_request", transport)
    with closing(sqlite3.connect(tmp_path / "reply.sqlite")) as db:
        initialize_database_schema(db)
        db.execute(
            "INSERT INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,world_file,"
            "created_at,updated_at) VALUES('c','s','Story','','m','','',1,1)"
        )
        db.execute("INSERT INTO meta(key,value) VALUES('stream_mode:c','off')")
        db.commit()
        assert mark_started(db, "c", "s", 0)
        session = {"session_id": "s", "model_id": "m", "character_file": "", "persona_id": "", "world_file": ""}
        with caplog.at_level(logging.INFO), performance.operation_scope(7, app_settings=config):
            generate_and_store_reply(
                db,
                "synthetic token",
                "synthetic credential",
                card_fields({"name": "Fixture"}, app_settings=config),
                "c",
                "private story text",
                session,
                "s",
                "m",
                None,
                "",
                None,
                None,
                group_service=make_test_group_service(app_settings=config),
                provider_port=ProviderPort(provider),
                memory_service=replace(make_test_memory_service(), resolve_scope=scoped_memory),
                npc_service=make_test_npc_service(),
                persona_service=make_test_persona_service(),
                app_settings=config,
                rag_service=make_test_rag_service(),
            )
        assert db.execute("SELECT role,telegram_message_ids FROM messages ORDER BY id").fetchall() == [
            ("user", "[]"),
            ("assistant", "[41]"),
        ]
    messages = [record.getMessage() for record in caplog.records if record.getMessage().startswith("perf ")]
    for name, duration in (
        ("memory_context", 30),
        ("context_assembly", 30),
        ("provider_stream", 50),
        ("reply_delivery", 20),
    ):
        assert any(f"span={name} duration_ms={duration:.3f} operation_id=job-7" in message for message in messages)
    assert not any(
        secret in "\n".join(messages)
        for secret in ("private story text", "synthetic credential", "synthetic token", "A synthetic reply")
    )


def test_disabled_guard_does_not_query_queue_metadata(tmp_path):
    with closing(sqlite3.connect(tmp_path / "guard.sqlite")) as db:
        initialize_database_schema(db)
        queries = []
        db.set_trace_callback(queries.append)
        guard = DurableWorkerGuard(lambda *_a, **_k: None, app_settings=settings(tmp_path, enabled=False))
        assert guard.prepare(db, 7, lambda: 42)() == 42
        assert queries == ["PRAGMA database_list"]


def test_unavailable_queue_metadata_does_not_block_worker(tmp_path, caplog):
    # Timing must not turn a failed optional metadata read into lost dispatch.
    class Connection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.startswith("SELECT CASE WHEN attempts=0"):
                raise sqlite3.OperationalError("database is locked")
            return super().execute(sql, parameters)

    with closing(sqlite3.connect(tmp_path / "guard.sqlite", factory=Connection)) as db:
        initialize_database_schema(db)
        guard = DurableWorkerGuard(lambda *_a, **_k: None, app_settings=settings(tmp_path))
        with caplog.at_level(logging.INFO):
            assert guard.prepare(db, 7, lambda: 42)() == 42
        assert any(
            "span=operation_execution " in record.getMessage() and "operation_id=job-7" in record.getMessage()
            for record in caplog.records
        )
        assert not any("span=queue_wait " in record.getMessage() for record in caplog.records)
