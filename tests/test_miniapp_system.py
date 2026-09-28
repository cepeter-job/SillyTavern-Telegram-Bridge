import json

import pytest
from miniapp_test_support import identity, make_services


def setup(tmp_path):
    from bridge.miniapp_context import current_session

    s = make_services(tmp_path)
    w = identity()
    current = current_session(s, w, {})["session"]
    return s, w, {"session_id": current["session_id"]}


def test_status_is_scoped_and_does_not_expose_runtime_secrets(tmp_path):
    from bridge.miniapp_system import system_status

    s, w, _p = setup(tmp_path)
    result = system_status(s, w, {})
    assert result["database"]["sessions"] == 1
    assert s.config.bot_token not in json.dumps(result)
    assert str(tmp_path) not in json.dumps(result)
    assert result["telegram"]["state"] == "unobserved"


def test_update_requires_owned_fresh_confirmation_and_reuses_guarded_engine(tmp_path, monkeypatch):
    import bridge.miniapp_system as system
    from bridge.self_update import UpdateOutcome, UpdateStatus

    s, w, p = setup(tmp_path)
    monkeypatch.setattr(system, "latest_bridge_release", lambda: ("0.2.099", "Reviewed release"))
    monkeypatch.setattr(system, "installed_bridge_version", lambda **kwargs: "0.2.032")
    calls = []

    def updated(**kwargs):
        calls.append(kwargs["expected_version"])
        return UpdateOutcome(UpdateStatus.RESTART_SCHEDULED, "0.2.099", "a" * 40)

    monkeypatch.setattr(system, "_run_update", updated)
    review = system.review_update(s, w, {})
    with pytest.raises(ValueError):
        system.perform_update(
            s, identity("67890"), {**p, "confirmation": review["confirmation"], "version": "0.2.099", "confirm": True}
        )
    with pytest.raises(ValueError):
        system.perform_update(
            s, w, {**p, "confirmation": review["confirmation"], "version": "0.2.098", "confirm": True}
        )
    result = system.perform_update(
        s, w, {**p, "confirmation": review["confirmation"], "version": "0.2.099", "confirm": True}
    )
    assert calls == ["0.2.099"]
    assert result["status"] == "restart_scheduled"
    with pytest.raises(ValueError):
        system.perform_update(
            s, w, {**p, "confirmation": review["confirmation"], "version": "0.2.099", "confirm": True}
        )


def test_update_review_does_not_confirm_already_latest_and_invalidates_stale_review(tmp_path, monkeypatch):
    import bridge.miniapp_system as system

    s, w, p = setup(tmp_path)
    installed = {"version": "0.2.032"}
    monkeypatch.setattr(system, "latest_bridge_release", lambda: ("0.2.099", "Reviewed release"))
    monkeypatch.setattr(system, "installed_bridge_version", lambda **kwargs: installed["version"])

    previous = system.review_update(s, w, {})
    assert previous["update_available"] is True
    installed["version"] = "0.2.099"
    current = system.review_update(s, w, {})

    assert current["update_available"] is False
    assert current["installed"] == current["latest"] == "0.2.099"
    assert "confirmation" not in current
    with pytest.raises(system.MiniAppError, match="expired or changed"):
        system.perform_update(
            s,
            w,
            {**p, "confirmation": previous["confirmation"], "version": "0.2.099", "confirm": True},
        )


def test_update_apply_rejects_already_latest_race(tmp_path, monkeypatch):
    import bridge.miniapp_system as system
    from bridge.self_update import UpdateOutcome, UpdateStatus

    s, w, p = setup(tmp_path)
    monkeypatch.setattr(system, "latest_bridge_release", lambda: ("0.2.099", "Reviewed release"))
    monkeypatch.setattr(system, "installed_bridge_version", lambda **kwargs: "0.2.032")
    review = system.review_update(s, w, {})
    monkeypatch.setattr(
        system,
        "_run_update",
        lambda **kwargs: UpdateOutcome(UpdateStatus.ALREADY_LATEST, "0.2.099"),
    )

    with pytest.raises(system.MiniAppError, match="Already latest") as exc:
        system.perform_update(
            s,
            w,
            {**p, "confirmation": review["confirmation"], "version": "0.2.099", "confirm": True},
        )
    assert exc.value.status == 409
    assert exc.value.code == "already_latest"


def test_health_snapshot_is_bound_to_boot_not_changed_files(tmp_path, monkeypatch):
    import bridge.runtime_health as health

    s, _w, _p = setup(tmp_path)
    monkeypatch.setattr(health, "capture_deployment", lambda settings: health.DeploymentIdentity("0.2.032", "a" * 40))
    runtime = health.RuntimeHealth()
    runtime.begin(s.config)
    monkeypatch.setattr(health, "capture_deployment", lambda settings: health.DeploymentIdentity("0.2.099", "b" * 40))
    runtime.begin(s.config)
    assert runtime.snapshot()["deployment"]["commit"] == "a" * 40
    assert runtime.snapshot()["telegram"]["state"] == "starting"
    runtime.poll_succeeded()
    assert runtime.snapshot()["telegram"]["state"] == "polling"
    runtime.poll_failed()
    assert runtime.snapshot()["telegram"]["state"] == "degraded"


@pytest.mark.parametrize("dirty", [False, True])
def test_loaded_dirty_checkout_cannot_be_reported_as_verified_commit(tmp_path, monkeypatch, dirty):
    import subprocess
    from dataclasses import replace

    import bridge.runtime_health as health

    s, _w, _p = setup(tmp_path)
    root = tmp_path / "source"
    (root / "bridge").mkdir(parents=True)
    (root / "CHANGELOG.md").write_text("## [0.2.099]\n")
    settings = replace(s.config, update_repo_dir=root)
    monkeypatch.setattr(health, "__file__", str(root / "bridge/runtime_health.py"))
    monkeypatch.setattr(health.shutil, "which", lambda name: "/usr/bin/git")

    def run(argv, **kwargs):
        output = (" M bridge/main.py\n" if dirty else "") if "status" in argv else "a" * 40 + "\n"
        return subprocess.CompletedProcess(argv, 0, output, "")

    monkeypatch.setattr(health.subprocess, "run", run)
    result = health.capture_deployment(settings)
    assert result.commit == ("" if dirty else "a" * 40)
