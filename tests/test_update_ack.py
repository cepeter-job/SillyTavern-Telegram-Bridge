from __future__ import annotations

import json
from pathlib import Path

import bridge.update as update
from settings_test_support import make_test_settings


def _settings(tmp_path: Path):
    bridge_home = tmp_path / "bridge-home"
    live = bridge_home / "live"
    live.mkdir(parents=True)
    return make_test_settings(
        home=tmp_path,
        bridge_home=bridge_home,
        update_live_dir=live,
    )


def test_pending_update_ack_is_written_atomically_with_expected_identity(tmp_path):
    from bridge.update_ack import arm_pending_update_ack, pending_update_ack_path

    settings = _settings(tmp_path)
    arm_pending_update_ack("12345", "0.2.033", app_settings=settings)

    path = pending_update_ack_path(settings)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {"format": 1, "chat_id": "12345", "version": "0.2.033"}
    assert path.stat().st_mode & 0o077 == 0


def test_successful_startup_ack_is_one_shot(tmp_path):
    from bridge.update_ack import acknowledge_pending_update, arm_pending_update_ack, pending_update_ack_path

    settings = _settings(tmp_path)
    arm_pending_update_ack("12345", "0.2.033", app_settings=settings)
    calls = []

    delivered = acknowledge_pending_update(
        "token",
        app_settings=settings,
        version_backend=lambda **_kwargs: "0.2.033",
        send_text_backend=lambda token, chat_id, text: calls.append((token, chat_id, text)),
    )

    assert delivered is True
    assert calls == [("token", "12345", "✅ Update complete — Running v0.2.033 successfully.")]
    assert not pending_update_ack_path(settings).exists()


def test_version_mismatch_never_claims_update_success(tmp_path):
    from bridge.update_ack import acknowledge_pending_update, arm_pending_update_ack, pending_update_ack_path

    settings = _settings(tmp_path)
    arm_pending_update_ack("12345", "0.2.033", app_settings=settings)
    calls = []

    delivered = acknowledge_pending_update(
        "token",
        app_settings=settings,
        version_backend=lambda **_kwargs: "0.2.032",
        send_text_backend=lambda *args: calls.append(args),
    )

    assert delivered is False
    assert calls == []
    assert pending_update_ack_path(settings).exists()


def test_malformed_pending_ack_never_blocks_startup(tmp_path):
    from bridge.update_ack import acknowledge_pending_update, pending_update_ack_path

    settings = _settings(tmp_path)
    path = pending_update_ack_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json", encoding="utf-8")

    assert acknowledge_pending_update(
        "token",
        app_settings=settings,
        version_backend=lambda **_kwargs: "0.2.033",
        send_text_backend=lambda *_args: (_ for _ in ()).throw(AssertionError("must not send")),
    ) is False
    assert not path.exists()


def test_failed_telegram_ack_is_retried_on_later_startup(tmp_path):
    from bridge.update_ack import acknowledge_pending_update, arm_pending_update_ack, pending_update_ack_path

    settings = _settings(tmp_path)
    arm_pending_update_ack("12345", "0.2.033", app_settings=settings)

    assert acknowledge_pending_update(
        "token",
        app_settings=settings,
        version_backend=lambda **_kwargs: "0.2.033",
        send_text_backend=lambda *_args: (_ for _ in ()).throw(OSError("telegram unavailable")),
    ) is False
    assert pending_update_ack_path(settings).exists()


def test_update_confirmation_arms_ack_before_restart_status(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    events = []
    outcome = update.UpdateOutcome(update.UpdateStatus.RESTART_SCHEDULED, "0.2.033")

    monkeypatch.setattr(update, "_run_update", lambda **_kwargs: outcome)
    monkeypatch.setattr(update, "answer_callback", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(update, "remove_inline_keyboard", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        update,
        "arm_pending_update_ack",
        lambda chat_id, version, *, app_settings: events.append(("arm", chat_id, version)),
        raising=False,
    )
    monkeypatch.setattr(
        update,
        "send_text",
        lambda token, chat_id, text: events.append(("send", chat_id, text)),
    )

    assert update.handle_update_callback(
        None,
        "token",
        {"id": "callback"},
        "update:confirm:0.2.033",
        "12345",
        app_settings=settings,
    ) is True

    assert events[0] == ("arm", "12345", "0.2.033")
    assert events[1][0:2] == ("send", "12345")
    assert "Restarting bridge" in events[1][2]


def test_restart_scheduled_status_does_not_claim_restart_success():
    outcome = update.UpdateOutcome(update.UpdateStatus.RESTART_SCHEDULED, "0.2.033")
    text = update.format_update_outcome(outcome)

    assert "Verified v0.2.033 installed." in text
    assert "Restarting bridge" in text
    assert "restart was requested" not in text
