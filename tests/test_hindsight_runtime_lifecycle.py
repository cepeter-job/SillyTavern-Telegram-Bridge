"""Optional recall closes between foreground worker drain and database teardown."""

import sqlite3
from types import SimpleNamespace

import pytest
from settings_test_support import make_test_settings

from bridge import runtime_lifecycle as lifecycle
from bridge.schema import initialize_database_schema


def shutdown_services(events, *, closes=True):
    return SimpleNamespace(
        config=make_test_settings(),
        background=SimpleNamespace(begin_shutdown=lambda: events.append("reject background")),
        hindsight_recall=SimpleNamespace(
            begin_shutdown=lambda: events.append("reject recall"),
            close=lambda **kwargs: events.append(("close recall", kwargs["timeout"])) or closes,
        ),
        memory_diagnostics=None,
        health=None,
    )


def prepare_shutdown(monkeypatch, events, *, background_error=False):
    def background(**kwargs):
        events.append("drain background")
        if background_error:
            raise RuntimeError("synthetic background cleanup error")
        return True

    monkeypatch.setattr(lifecycle, "shutdown_background_executors", background)
    monkeypatch.setattr(lifecycle, "stop_live_sync_worker", lambda **kwargs: events.append("stop sync") or True)
    monkeypatch.setattr(lifecycle, "run_database_maintenance", lambda **kwargs: events.append("maintenance"))
    monkeypatch.setattr(lifecycle, "_arm_forced_exit_watchdog", lambda: events.append("watchdog"))


@pytest.mark.parametrize("background_error", [False, True])
def test_shutdown_closes_recall_after_background_even_if_other_cleanup_raises(monkeypatch, background_error):
    events = []
    services = shutdown_services(events)
    prepare_shutdown(monkeypatch, events, background_error=background_error)
    db = SimpleNamespace(close=lambda: events.append("close db"))
    try:
        if background_error:
            with pytest.raises(RuntimeError, match="background cleanup"):
                lifecycle._shutdown_runtime(services, db, allow_maintenance=True)
        else:
            lifecycle._shutdown_runtime(services, db, allow_maintenance=True)
        assert events[:2] == ["reject background", "reject recall"]
        assert events.index("drain background") < events.index(("close recall", 3.0)) < events.index("close db")
        assert ("maintenance" in events) is (not background_error)
    finally:
        lifecycle._SHUTDOWN_EVENT.clear()


@pytest.mark.parametrize("raises", [False, True])
def test_failed_recall_close_arms_watchdog_and_suppresses_maintenance(monkeypatch, raises):
    events = []
    services = shutdown_services(events, closes=False)
    prepare_shutdown(monkeypatch, events)
    if raises:

        def failed(**kwargs):
            raise RuntimeError("synthetic SDK cleanup error")

        services.hindsight_recall.close = failed
    try:
        lifecycle._shutdown_runtime(
            services, SimpleNamespace(close=lambda: events.append("close db")), allow_maintenance=True
        )
        assert events.count("watchdog") == 1
        assert "close db" in events
        assert "maintenance" not in events
    finally:
        lifecycle._SHUTDOWN_EVENT.clear()


@pytest.mark.parametrize("exit_kind", ["signal", "keyboard", "startup failure", "optional startup failure"])
def test_runtime_starts_before_work_and_rejects_recall_on_every_exit(monkeypatch, tmp_path, exit_kind):
    events = []
    services = shutdown_services(events)
    prepare_shutdown(monkeypatch, events)
    db = sqlite3.connect(":memory:")
    initialize_database_schema(db)
    services.config = make_test_settings()
    installed = []

    def start():
        events.append("start recall")
        if exit_kind == "optional startup failure":
            raise ImportError("synthetic optional startup error")

    def poll(*args, **kwargs):
        events.append("poll")
        if exit_kind == "keyboard":
            raise KeyboardInterrupt
        lifecycle.request_bridge_shutdown(installed[0])
        return []

    def database():
        if exit_kind == "startup failure":
            raise RuntimeError("synthetic core startup failure")
        return db

    services.hindsight_recall.start = start
    services.db_factory = database
    services.telegram = SimpleNamespace(request=poll)
    services.jobs = SimpleNamespace(recover=lambda *args, **kwargs: None)
    services.sync = object()
    services.background.submit = lambda *args, **kwargs: events.append("submit") or True
    services.background.register_backlog_dispatcher = lambda callback: None
    monkeypatch.setattr(lifecycle, "install_bridge_signal_handlers", lambda callback: installed.append(callback))
    monkeypatch.setattr(
        lifecycle, "capture_deployment", lambda config: SimpleNamespace(version="test", commit="0" * 40)
    )
    monkeypatch.setattr(lifecycle, "dispatch_memory_backlog", lambda *args, **kwargs: events.append("work"))
    monkeypatch.setattr(lifecycle, "start_live_sync_worker", lambda **kwargs: None)
    monkeypatch.setattr(lifecycle, "make_durable_backlog_dispatcher", lambda *args: lambda: None)
    monkeypatch.setattr(lifecycle, "_queue_ending_recovery", lambda *args: 0)
    monkeypatch.setattr(lifecycle, "pending_update_ack_path", lambda config: tmp_path / "absent")
    try:
        if exit_kind == "startup failure":
            with pytest.raises(RuntimeError, match="core startup"):
                lifecycle.run_bridge_runtime(services, {})
        else:
            assert lifecycle.run_bridge_runtime(services, {}) == 0
            assert events.index("start recall") < events.index("work")
        assert "reject recall" in events and ("close recall", 3.0) in events
        assert events.index("reject recall") < events.index("drain background")
    finally:
        db.close()
        lifecycle._SHUTDOWN_EVENT.clear()
