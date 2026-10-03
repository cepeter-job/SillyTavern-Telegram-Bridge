from __future__ import annotations

import sqlite3
from types import SimpleNamespace


def test_forced_exit_watchdog_uses_clean_process_exit(monkeypatch):
    import bridge.runtime_lifecycle as lifecycle

    calls = []
    monkeypatch.setattr(lifecycle.time, "sleep", lambda delay: calls.append(("sleep", delay)))
    monkeypatch.setattr(lifecycle.os, "_exit", lambda code: calls.append(("exit", code)))

    thread = lifecycle._arm_forced_exit_watchdog(0.25)
    thread.join(timeout=1.0)

    assert calls == [("sleep", 0.25), ("exit", 0)]


def _exercise_shutdown(monkeypatch, tmp_path, *, drained: bool):
    import bridge.runtime_lifecycle as lifecycle

    database = sqlite3.connect(":memory:")
    database.execute("CREATE TABLE light_novel_choice_sets(generation_status TEXT, lease_token TEXT, lease_until REAL)")
    events = []

    def request(_token, method, _payload=None):
        assert method == "getUpdates"
        lifecycle._SHUTDOWN_EVENT.set()
        return []

    config = SimpleNamespace(bot_token="token", allowed_users=frozenset(), bridge_home=tmp_path / "state", environ={})
    services = SimpleNamespace(
        config=config,
        db_factory=lambda: database,
        telegram=SimpleNamespace(request=request, send_text=lambda *_args: None),
        background=SimpleNamespace(
            begin_shutdown=lambda: None,
            submit=lambda *_args, **_kwargs: True,
            register_backlog_dispatcher=lambda _callback: None,
        ),
        jobs=SimpleNamespace(recover=lambda *_args, **_kwargs: None),
        sync=object(),
        health=None,
        memory_diagnostics=None,
    )
    monkeypatch.setattr(
        lifecycle,
        "capture_deployment",
        lambda _config: SimpleNamespace(version="0.2.057", commit="a" * 40),
    )
    monkeypatch.setattr(lifecycle, "install_bridge_signal_handlers", lambda *_args: None)
    monkeypatch.setattr(lifecycle, "start_live_sync_worker", lambda **_kwargs: None)
    monkeypatch.setattr(lifecycle, "stop_live_sync_worker", lambda **_kwargs: True)
    monkeypatch.setattr(lifecycle, "shutdown_background_executors", lambda **_kwargs: drained)
    monkeypatch.setattr(lifecycle, "run_database_maintenance", lambda **_kwargs: events.append("maintenance"))
    monkeypatch.setattr(lifecycle, "_arm_forced_exit_watchdog", lambda *_args, **_kwargs: events.append("watchdog"))
    monkeypatch.setattr(lifecycle, "get_meta", lambda *_args, **_kwargs: "0")
    monkeypatch.setattr(lifecycle, "pending_update_ack_path", lambda _config: tmp_path / "no-pending")
    monkeypatch.setattr(lifecycle, "make_durable_backlog_dispatcher", lambda *_args, **_kwargs: lambda: None)

    try:
        assert lifecycle.run_bridge_runtime(services, {}) == 0
    finally:
        lifecycle._SHUTDOWN_EVENT.clear()
    return events


def test_stuck_background_shutdown_skips_maintenance_and_arms_watchdog(monkeypatch, tmp_path):
    events = _exercise_shutdown(monkeypatch, tmp_path, drained=False)
    assert events == ["watchdog"]


def test_clean_background_shutdown_runs_maintenance_without_watchdog(monkeypatch, tmp_path):
    events = _exercise_shutdown(monkeypatch, tmp_path, drained=True)
    assert events == ["maintenance"]
