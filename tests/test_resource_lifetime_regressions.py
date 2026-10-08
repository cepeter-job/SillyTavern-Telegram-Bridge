"""Failure paths must release owned handles before an exception escapes."""

from __future__ import annotations

import io
import sqlite3
import urllib.error
from types import SimpleNamespace

import pytest

from bridge import sqlite_store, telegram


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
@pytest.mark.parametrize(
    "opener,stage",
    [
        ("_open_initialized_database", "_apply_connection_pragmas"),
        ("_open_initialized_database", "initialize_database_schema"),
        ("_lightweight_db_connect", "_apply_connection_pragmas"),
    ],
)
def test_failed_database_open_closes_handle_and_releases_writer(monkeypatch, tmp_path, opener, stage, failure):
    opened = []

    def fail(db, *args, **kwargs):
        opened.append(db)
        db.execute("CREATE TABLE IF NOT EXISTS probe(value INTEGER)")
        db.execute("INSERT INTO probe VALUES (1)")
        raise failure("injected initialization failure")

    monkeypatch.setattr(sqlite_store, stage, fail)
    try:
        with pytest.raises(failure, match="injected initialization failure"):
            getattr(sqlite_store, opener)(tmp_path / "failed.sqlite3")
        assert len(opened) == 1
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            opened[0].execute("SELECT 1")
        assert not opened[0]._bridge_write_lock_held
    finally:
        for db in opened:
            db.close()


@pytest.mark.parametrize("body", [b'{"description":"denied"}', b"not JSON"])
def test_telegram_error_closes_body_before_exception_escapes(monkeypatch, body):
    stream = io.BytesIO(body)
    error = urllib.error.HTTPError("https://api.telegram.org/fixture", 403, "Forbidden", {}, stream)

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(telegram.urllib.request, "urlopen", fail)
    try:
        with pytest.raises(RuntimeError, match="Telegram getUpdates failed"):
            telegram.telegram_request("fixture", "getUpdates", {})
        assert stream.closed
    finally:
        error.close()


def test_telegram_retry_closes_previous_error_before_sleep(monkeypatch):
    errors = [
        urllib.error.HTTPError(
            "https://api.telegram.org/fixture", 404, "Not Found", {}, io.BytesIO(b'{"description":"Not Found"}')
        )
        for _ in range(3)
    ]
    pending = iter(errors)

    def fail(*args, **kwargs):
        raise next(pending)

    def sleep(_delay):
        assert all(error.fp.closed for error in errors if error.__traceback__ is not None)

    monkeypatch.setattr(telegram.urllib.request, "urlopen", fail)
    monkeypatch.setattr(telegram.time, "sleep", sleep)
    try:
        with pytest.raises(RuntimeError, match="Telegram sendMessage failed"):
            telegram.telegram_request("fixture", "sendMessage", {"text": "test"})
        assert all(error.fp.closed for error in errors)
    finally:
        for error in errors:
            error.close()


@pytest.mark.parametrize("stage", ["database", "sync", "recovery"])
@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_runtime_startup_failure_releases_acquired_resources(monkeypatch, tmp_path, stage, failure):
    import bridge.runtime_lifecycle as lifecycle

    db = sqlite3.connect(":memory:")
    from bridge.schema import initialize_database_schema

    initialize_database_schema(db)
    events = []

    def fail():
        raise failure("startup failed")

    def open_database():
        return fail() if stage == "database" else db

    def start_sync(**kwargs):
        events.append("sync-start")
        if stage == "sync":
            fail()

    def recover(*args, **kwargs):
        fail()

    config = SimpleNamespace(bot_token="fixture", allowed_users=frozenset(), bridge_home=tmp_path, environ={})
    services = SimpleNamespace(
        config=config,
        health=None,
        db_factory=open_database,
        sync=object(),
        background=SimpleNamespace(
            begin_shutdown=lambda: events.append("admission-stop"),
            submit=lambda *_args, **_kwargs: True,
            register_backlog_dispatcher=lambda callback: None,
        ),
        jobs=SimpleNamespace(recover=recover),
        memory_diagnostics=SimpleNamespace(
            start=lambda: events.append("diagnostics-start"), stop=lambda **kwargs: events.append("diagnostics-stop")
        ),
    )
    monkeypatch.setattr(lifecycle, "capture_deployment", lambda config: SimpleNamespace(version="test", commit="test"))
    monkeypatch.setattr(lifecycle, "pending_update_ack_path", lambda config: tmp_path / "missing")
    monkeypatch.setattr(lifecycle, "install_bridge_signal_handlers", lambda callback: None)
    monkeypatch.setattr(lifecycle, "start_live_sync_worker", start_sync)
    monkeypatch.setattr(lifecycle, "stop_live_sync_worker", lambda **kwargs: events.append("sync-stop") or True)
    monkeypatch.setattr(
        lifecycle, "shutdown_background_executors", lambda **kwargs: events.append("workers-stop") or True
    )
    monkeypatch.setattr(lifecycle, "run_database_maintenance", lambda **kwargs: events.append("maintenance"))
    monkeypatch.setattr(lifecycle, "make_durable_backlog_dispatcher", lambda *args: lambda: None)
    try:
        with pytest.raises(failure, match="startup failed"):
            lifecycle.run_bridge_runtime(services, {})
        assert "diagnostics-stop" in events
        assert "workers-stop" in events
        assert "admission-stop" in events
        assert "maintenance" not in events
        if stage != "database":
            assert "sync-stop" in events
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                db.execute("SELECT 1")
    finally:
        db.close()
        lifecycle._SHUTDOWN_EVENT.clear()


def test_failed_sync_cleanup_still_closes_database_and_background(monkeypatch, tmp_path):
    import bridge.runtime_lifecycle as lifecycle

    db = sqlite3.connect(":memory:")
    events = []
    services = SimpleNamespace(
        config=object(), health=None, memory_diagnostics=None, background=SimpleNamespace(begin_shutdown=lambda: None)
    )

    def fail_sync(**kwargs):
        raise RuntimeError("sync cleanup failed")

    monkeypatch.setattr(lifecycle, "stop_live_sync_worker", fail_sync)
    monkeypatch.setattr(
        lifecycle, "shutdown_background_executors", lambda **kwargs: events.append("workers-stop") or True
    )
    try:
        with pytest.raises(RuntimeError, match="sync cleanup failed"):
            lifecycle._shutdown_runtime(services, db, allow_maintenance=False)
        assert events == ["workers-stop"]
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            db.execute("SELECT 1")
    finally:
        db.close()
        lifecycle._SHUTDOWN_EVENT.clear()


def test_polling_http_error_closes_response_before_retry_wait(monkeypatch, tmp_path):
    from miniapp_test_support import make_services

    import bridge.runtime_lifecycle as lifecycle

    services = make_services(tmp_path)
    stream = io.BytesIO(b"retry later")
    error = urllib.error.HTTPError("https://api.telegram.org/fixture", 503, "Unavailable", {}, stream)

    def request(*args, **kwargs):
        raise error

    def wait(timeout):
        assert stream.closed
        lifecycle._SHUTDOWN_EVENT.set()
        return True

    services.telegram = SimpleNamespace(request=request, send_text=lambda *args: None)
    services.background = SimpleNamespace(
        begin_shutdown=lambda: None,
        submit=lambda *_args, **_kwargs: True,
        register_backlog_dispatcher=lambda callback: None,
    )
    services.jobs = SimpleNamespace(recover=lambda *args, **kwargs: None)
    monkeypatch.setattr(lifecycle, "capture_deployment", lambda config: SimpleNamespace(version="test", commit="test"))
    monkeypatch.setattr(lifecycle, "install_bridge_signal_handlers", lambda callback: None)
    monkeypatch.setattr(lifecycle, "start_live_sync_worker", lambda **kwargs: None)
    monkeypatch.setattr(lifecycle, "stop_live_sync_worker", lambda **kwargs: True)
    monkeypatch.setattr(lifecycle, "shutdown_background_executors", lambda **kwargs: True)
    monkeypatch.setattr(lifecycle, "run_database_maintenance", lambda **kwargs: None)
    monkeypatch.setattr(lifecycle._SHUTDOWN_EVENT, "wait", wait)
    try:
        assert lifecycle.run_bridge_runtime(services, {}) == 0
    finally:
        error.close()
        lifecycle._SHUTDOWN_EVENT.clear()


def test_miniapp_fixture_bootstraps_imports_without_pytest_paths(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    fixture = Path(__file__).with_name("miniapp_browser_fixture.py").resolve()
    probe = (
        "import runpy, sys; from pathlib import Path; "
        "sys.path.insert(0, str(Path(sys.argv[1]).parent)); "
        "runpy.run_path(sys.argv[1], run_name='fixture_import_check')"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", probe, str(fixture)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("operation", ["recall", "retain"])
@pytest.mark.parametrize("fails", [False, True])
def test_hindsight_operations_close_owned_event_loops(monkeypatch, tmp_path, operation, fails):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    from hindsight_client import Hindsight
    from settings_test_support import make_test_settings

    from bridge import memory_backend
    from bridge.hindsight_recall_runtime import HindsightRecallRuntime

    loops = []
    original_init = asyncio.BaseEventLoop.__init__

    def track(loop, *args, **kwargs):
        original_init(loop, *args, **kwargs)
        loops.append(loop)

    def response(self, **kwargs):
        if fails:
            raise RuntimeError("synthetic Hindsight failure")
        return SimpleNamespace(results=[], success=True)

    monkeypatch.setattr(asyncio.BaseEventLoop, "__init__", track)
    monkeypatch.setattr(Hindsight, operation, response)
    monkeypatch.setattr(memory_backend, "memory_mode", lambda *args: "on")
    settings = make_test_settings(home=tmp_path, environ={"HINDSIGHT_API_URL": "http://127.0.0.1:8890"})

    runtime = None
    if operation == "recall":

        async def async_response(self, **kwargs):
            return response(self, **kwargs)

        monkeypatch.setattr(Hindsight, "arecall", async_response)
        runtime = HindsightRecallRuntime(base_url="http://127.0.0.1:8890", api_key=None)
        runtime.start()

    def exercise():
        if operation == "recall":
            import sqlite3

            from bridge.schema import initialize_database_schema

            db = sqlite3.connect(":memory:")
            try:
                initialize_database_schema(db)
                db.execute(
                    "INSERT INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,"
                    "world_file,created_at,updated_at) VALUES('chat','session','Story','','model','','',1,1)"
                )
                db.execute(
                    "INSERT INTO messages(chat_id,session_id,role,content,created_at) "
                    "VALUES('chat','session','user','Remember a fact.',1)"
                )
                db.commit()
                return memory_backend.recall_memory_results(
                    db,
                    "chat",
                    {"session_id": "session"},
                    "query",
                    "Character",
                    app_settings=settings,
                    remote_recall=runtime.recall,
                )
            finally:
                db.close()
        return memory_backend._retain_with_client(
            "chat", "session", "document", "character", "content", "context", "native_fact", app_settings=settings
        )

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            for _ in range(3):
                executor.submit(exercise).result(timeout=10)
        if runtime is not None:
            assert runtime.close()
        assert loops, "The real SDK close path must exercise event-loop ownership"
        assert all(loop.is_closed() for loop in loops)
    finally:
        if runtime is not None:
            assert runtime.close()
        for loop in loops:
            if not loop.is_closed():
                loop.close()


@pytest.mark.parametrize("stage", ["constructor", "cleanup"])
def test_hindsight_owned_loop_closes_when_lifecycle_fails(monkeypatch, tmp_path, stage):
    import asyncio

    from settings_test_support import make_test_settings

    from bridge import memory_backend

    loops = []

    def fail_close():
        raise RuntimeError("cleanup failed")

    def factory(**kwargs):
        loops.append(asyncio.get_event_loop())
        if stage == "constructor":
            raise RuntimeError("constructor failed")
        return SimpleNamespace(close=fail_close)

    monkeypatch.setattr(memory_backend, "hindsight_client", factory)
    settings = make_test_settings(home=tmp_path)
    if stage == "constructor":
        with pytest.raises(RuntimeError, match="constructor failed"):
            with memory_backend.hindsight_client_scope(app_settings=settings):
                pytest.fail("The constructor must fail before yielding a client")
    else:
        with memory_backend.hindsight_client_scope(app_settings=settings):
            pass
    assert len(loops) == 1
    assert loops[0].is_closed()
