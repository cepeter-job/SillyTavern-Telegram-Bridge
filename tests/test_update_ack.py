from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from settings_test_support import make_test_settings

import bridge.update as update
from bridge.update_ack import UpdateAckStatus


def _settings(tmp_path: Path):
    bridge_home = tmp_path / "bridge-home"
    live = bridge_home / "live"
    live.mkdir(parents=True)
    bridge_home.chmod(0o700)
    return make_test_settings(
        home=tmp_path,
        bridge_home=bridge_home,
        update_live_dir=live,
    )


def test_pending_update_ack_is_written_atomically_with_expected_identity(tmp_path):
    from bridge.update_ack import arm_pending_update_ack, pending_update_ack_path

    settings = _settings(tmp_path)
    arm_pending_update_ack("12345", "0.2.033", commit="a" * 40, app_settings=settings)

    path = pending_update_ack_path(settings)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["format"] == 2
    assert payload["chat_id"] == "12345"
    assert payload["version"] == "0.2.033"
    assert payload["commit"] == "a" * 40
    assert payload["created_at"] > 0
    assert path.stat().st_mode & 0o077 == 0


def test_successful_startup_ack_is_one_shot(tmp_path):
    from bridge.update_ack import arm_pending_update_ack, attempt_pending_update_ack, pending_update_ack_path

    settings = _settings(tmp_path)
    arm_pending_update_ack("12345", "0.2.033", commit="a" * 40, app_settings=settings)
    calls = []

    delivered = attempt_pending_update_ack(
        "token",
        app_settings=settings,
        commit_backend=lambda **_kwargs: "a" * 40,
        version_backend=lambda **_kwargs: "0.2.033",
        send_text_backend=lambda token, chat_id, text: calls.append((token, chat_id, text)),
    )

    assert delivered is UpdateAckStatus.DELIVERED
    assert calls == [("token", "12345", "✅ Update complete — Running v0.2.033 successfully.")]
    assert not pending_update_ack_path(settings).exists()


def test_version_mismatch_never_claims_update_success(tmp_path):
    from bridge.update_ack import arm_pending_update_ack, attempt_pending_update_ack, pending_update_ack_path

    settings = _settings(tmp_path)
    arm_pending_update_ack("12345", "0.2.033", commit="a" * 40, app_settings=settings)
    calls = []

    delivered = attempt_pending_update_ack(
        "token",
        app_settings=settings,
        commit_backend=lambda **_kwargs: "a" * 40,
        version_backend=lambda **_kwargs: "0.2.032",
        send_text_backend=lambda *args: calls.append(args),
    )

    assert delivered is UpdateAckStatus.REVISION_MISMATCH
    assert calls == []
    assert pending_update_ack_path(settings).exists()


def test_revision_mismatch_has_explicit_non_retryable_process_status(tmp_path):
    from bridge.update_ack import (
        UpdateAckStatus,
        arm_pending_update_ack,
        attempt_pending_update_ack,
        pending_update_ack_path,
    )

    settings = _settings(tmp_path)
    arm_pending_update_ack("12345", "0.2.033", commit="a" * 40, app_settings=settings)
    status = attempt_pending_update_ack(
        "token",
        app_settings=settings,
        commit_backend=lambda **_kwargs: "b" * 40,
        version_backend=lambda **_kwargs: "0.2.033",
        send_text_backend=lambda *_args: (_ for _ in ()).throw(AssertionError("must not send")),
    )

    assert status is UpdateAckStatus.REVISION_MISMATCH
    assert pending_update_ack_path(settings).exists()


def test_malformed_pending_ack_never_blocks_startup(tmp_path):
    from bridge.update_ack import attempt_pending_update_ack, pending_update_ack_path

    settings = _settings(tmp_path)
    path = pending_update_ack_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json", encoding="utf-8")

    assert (
        attempt_pending_update_ack(
            "token",
            app_settings=settings,
            commit_backend=lambda **_kwargs: "a" * 40,
            version_backend=lambda **_kwargs: "0.2.033",
            send_text_backend=lambda *_args: (_ for _ in ()).throw(AssertionError("must not send")),
        )
        is UpdateAckStatus.ABSENT
    )
    assert not path.exists()


def test_failed_telegram_ack_is_retried_on_later_startup(tmp_path):
    from bridge.update_ack import arm_pending_update_ack, attempt_pending_update_ack, pending_update_ack_path

    settings = _settings(tmp_path)
    arm_pending_update_ack("12345", "0.2.033", commit="a" * 40, app_settings=settings)

    assert (
        attempt_pending_update_ack(
            "token",
            app_settings=settings,
            commit_backend=lambda **_kwargs: "a" * 40,
            version_backend=lambda **_kwargs: "0.2.033",
            send_text_backend=lambda *_args: (_ for _ in ()).throw(OSError("telegram unavailable")),
        )
        is UpdateAckStatus.RETRYABLE_ERROR
    )
    assert pending_update_ack_path(settings).exists()


def test_update_confirmation_arms_ack_before_restart_status(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    events = []
    outcome = update.UpdateOutcome(update.UpdateStatus.RESTART_SCHEDULED, "0.2.033", "a" * 40)

    def run(**kwargs):
        kwargs["before_restart"](outcome)
        return outcome

    monkeypatch.setattr(update, "_run_update", run)
    monkeypatch.setattr(update, "answer_callback", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(update, "remove_inline_keyboard", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        update,
        "arm_pending_update_ack",
        lambda chat_id, version, *, commit, app_settings: events.append(("arm", chat_id, version)),
        raising=False,
    )
    monkeypatch.setattr(
        update,
        "send_text",
        lambda token, chat_id, text: events.append(("send", chat_id, text)),
    )

    assert (
        update.handle_update_callback(
            None,
            "token",
            {"id": "callback"},
            "update:confirm:0.2.033",
            "12345",
            app_settings=settings,
        )
        is True
    )

    assert events[0] == ("arm", "12345", "0.2.033")
    assert events[1][0:2] == ("send", "12345")
    assert "Restarting bridge" in events[1][2]


def test_restart_scheduled_status_does_not_claim_restart_success():
    outcome = update.UpdateOutcome(update.UpdateStatus.RESTART_SCHEDULED, "0.2.033")
    text = update.format_update_outcome(outcome)

    assert "Verified v0.2.033 installed." in text
    assert "Restarting bridge" in text
    assert "restart was requested" not in text


def test_normal_startup_does_not_claim_readiness_before_polling(monkeypatch, tmp_path):
    import bridge.main as main
    import bridge.runtime_lifecycle as lifecycle

    settings = _settings(tmp_path)
    services = SimpleNamespace(config=settings)
    events = []

    monkeypatch.setattr(sys, "argv", ["bridge"])
    monkeypatch.setattr(main, "bootstrap_environment", lambda _environment: None)
    monkeypatch.setattr(main, "_load_startup_config", lambda _environment: settings)
    monkeypatch.setattr(main, "_ModelRouter", lambda **_kwargs: object())
    monkeypatch.setattr(main, "validate_startup_credential", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main, "enforce_runtime_permissions", lambda **_kwargs: None)
    monkeypatch.setattr(main, "configure_logging", lambda **_kwargs: None)
    monkeypatch.setattr(main, "_build_startup_services", lambda *_args, **_kwargs: services)
    monkeypatch.setattr(main, "_initialize_extensions", lambda: None)
    monkeypatch.setattr(main, "set_bot_commands", lambda _token: events.append("commands"))
    monkeypatch.setattr(main, "read_png_chara", lambda _path: {})
    monkeypatch.setattr(main, "card_fields", lambda *_args, **_kwargs: {"name": "test"})
    monkeypatch.setattr(
        lifecycle,
        "attempt_pending_update_ack",
        lambda *_args, **_kwargs: events.append("ack"),
        raising=False,
    )
    monkeypatch.setattr(
        main,
        "run_bridge_runtime",
        lambda *_args, **_kwargs: events.append("runtime") or 0,
    )

    assert main._main() == 0
    assert events[-2:] == ["commands", "runtime"]
    assert "ack" not in events


def test_same_version_wrong_commit_does_not_acknowledge(tmp_path):
    from bridge.update_ack import arm_pending_update_ack, attempt_pending_update_ack

    settings = _settings(tmp_path)
    arm_pending_update_ack("12345", "0.2.033", commit="a" * 40, app_settings=settings)
    calls = []
    assert (
        attempt_pending_update_ack(
            "token",
            app_settings=settings,
            version_backend=lambda **kwargs: "0.2.033",
            commit_backend=lambda **kwargs: "b" * 40,
            send_text_backend=lambda *args: calls.append(args),
        )
        is UpdateAckStatus.REVISION_MISMATCH
    )
    assert not calls


def test_stale_acknowledgement_is_discarded(tmp_path):
    import time

    from bridge.update_ack import arm_pending_update_ack, attempt_pending_update_ack, pending_update_ack_path

    settings = _settings(tmp_path)
    arm_pending_update_ack("12345", "0.2.033", commit="a" * 40, app_settings=settings)
    path = pending_update_ack_path(settings)
    payload = json.loads(path.read_text())
    payload["created_at"] = time.time() - 86401
    path.write_text(json.dumps(payload))
    calls = []
    assert (
        attempt_pending_update_ack(
            "token",
            app_settings=settings,
            version_backend=lambda **kwargs: "0.2.033",
            commit_backend=lambda **kwargs: "a" * 40,
            send_text_backend=lambda *args: calls.append(args),
        )
        is UpdateAckStatus.ABSENT
    )
    assert not calls and not path.exists()


def test_completion_notification_is_sent_only_after_successful_poll(tmp_path, monkeypatch):
    from miniapp_test_support import make_services

    import bridge.runtime_lifecycle as lifecycle
    from bridge.runtime_health import DeploymentIdentity
    from bridge.update_ack import arm_pending_update_ack

    services = make_services(tmp_path)
    events = []

    def request(token, method, body):
        events.append("poll")
        if events.count("poll") > 1:
            raise KeyboardInterrupt()
        assert body["timeout"] == 0
        return []

    services.telegram = SimpleNamespace(request=request, send_text=lambda *args: events.append("ack"))
    services.background = SimpleNamespace(begin_shutdown=lambda: None, register_backlog_dispatcher=lambda x: None)
    services.jobs = SimpleNamespace(recover=lambda *args, **kwargs: None)
    monkeypatch.setattr(lifecycle, "capture_deployment", lambda config: DeploymentIdentity("0.2.033", "a" * 40))
    monkeypatch.setattr(lifecycle, "install_bridge_signal_handlers", lambda *args: None)
    monkeypatch.setattr(lifecycle, "start_live_sync_worker", lambda **kwargs: None)
    monkeypatch.setattr(lifecycle, "stop_live_sync_worker", lambda **kwargs: True)
    monkeypatch.setattr(lifecycle, "shutdown_background_executors", lambda **kwargs: True)
    monkeypatch.setattr(lifecycle, "run_database_maintenance", lambda **kwargs: None)
    arm_pending_update_ack("12345", "0.2.033", commit="a" * 40, app_settings=services.config)
    try:
        assert lifecycle.run_bridge_runtime(services, {}) == 0
        assert events == ["poll", "ack", "poll"]
    finally:
        lifecycle._SHUTDOWN_EVENT.clear()


def test_revision_mismatch_is_attempted_only_once_per_process(tmp_path, monkeypatch):
    from miniapp_test_support import make_services

    import bridge.runtime_lifecycle as lifecycle
    from bridge.runtime_health import DeploymentIdentity
    from bridge.update_ack import UpdateAckStatus, arm_pending_update_ack

    services = make_services(tmp_path)
    polls = 0
    attempts = []

    def request(_token, _method, _body):
        nonlocal polls
        polls += 1
        if polls > 3:
            raise KeyboardInterrupt()
        return []

    services.telegram = SimpleNamespace(request=request, send_text=lambda *_args: None)
    services.background = SimpleNamespace(begin_shutdown=lambda: None, register_backlog_dispatcher=lambda _x: None)
    services.jobs = SimpleNamespace(recover=lambda *args, **kwargs: None)
    monkeypatch.setattr(lifecycle, "capture_deployment", lambda _config: DeploymentIdentity("0.2.033", "a" * 40))
    monkeypatch.setattr(lifecycle, "install_bridge_signal_handlers", lambda *args: None)
    monkeypatch.setattr(lifecycle, "start_live_sync_worker", lambda **kwargs: None)
    monkeypatch.setattr(lifecycle, "stop_live_sync_worker", lambda **kwargs: True)
    monkeypatch.setattr(lifecycle, "shutdown_background_executors", lambda **kwargs: True)
    monkeypatch.setattr(lifecycle, "run_database_maintenance", lambda **kwargs: None)
    monkeypatch.setattr(
        lifecycle,
        "attempt_pending_update_ack",
        lambda *_args, **_kwargs: attempts.append(polls) or UpdateAckStatus.REVISION_MISMATCH,
    )
    arm_pending_update_ack("12345", "0.2.033", commit="a" * 40, app_settings=services.config)
    try:
        assert lifecycle.run_bridge_runtime(services, {}) == 0
        assert attempts == [1]
    finally:
        lifecycle._SHUTDOWN_EVENT.clear()


def test_retryable_ack_uses_bounded_exponential_backoff(tmp_path, monkeypatch):
    from miniapp_test_support import make_services

    import bridge.runtime_lifecycle as lifecycle
    from bridge.runtime_health import DeploymentIdentity
    from bridge.update_ack import UpdateAckStatus, arm_pending_update_ack

    services = make_services(tmp_path)
    polls = 0
    clock = [0.0]
    attempts = []

    def request(_token, _method, _body):
        nonlocal polls
        polls += 1
        if polls > 7:
            raise KeyboardInterrupt()
        clock[0] += 50.0
        return []

    services.telegram = SimpleNamespace(request=request, send_text=lambda *_args: None)
    services.background = SimpleNamespace(begin_shutdown=lambda: None, register_backlog_dispatcher=lambda _x: None)
    services.jobs = SimpleNamespace(recover=lambda *args, **kwargs: None)
    monkeypatch.setattr(lifecycle, "capture_deployment", lambda _config: DeploymentIdentity("0.2.033", "a" * 40))
    monkeypatch.setattr(lifecycle, "install_bridge_signal_handlers", lambda *args: None)
    monkeypatch.setattr(lifecycle, "start_live_sync_worker", lambda **kwargs: None)
    monkeypatch.setattr(lifecycle, "stop_live_sync_worker", lambda **kwargs: True)
    monkeypatch.setattr(lifecycle, "shutdown_background_executors", lambda **kwargs: True)
    monkeypatch.setattr(lifecycle, "run_database_maintenance", lambda **kwargs: None)
    monkeypatch.setattr(lifecycle.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        lifecycle,
        "attempt_pending_update_ack",
        lambda *_args, **_kwargs: attempts.append(clock[0]) or UpdateAckStatus.RETRYABLE_ERROR,
    )
    arm_pending_update_ack("12345", "0.2.033", commit="a" * 40, app_settings=services.config)
    try:
        assert lifecycle.run_bridge_runtime(services, {}) == 0
        assert attempts == [50.0, 150.0, 300.0]
    finally:
        lifecycle._SHUTDOWN_EVENT.clear()


def test_acknowledgement_preserves_native_forum_topic_scope(tmp_path):
    from bridge.update_ack import arm_pending_update_ack, attempt_pending_update_ack

    settings = _settings(tmp_path)
    scope = "-100123456789|topic:42"
    arm_pending_update_ack(scope, "0.2.033", commit="a" * 40, app_settings=settings)
    calls = []
    assert (
        attempt_pending_update_ack(
            "token",
            app_settings=settings,
            version_backend=lambda **kwargs: "0.2.033",
            commit_backend=lambda **kwargs: "a" * 40,
            send_text_backend=lambda token, chat, text: calls.append(chat),
        )
        is UpdateAckStatus.DELIVERED
    )
    assert calls == [scope]
