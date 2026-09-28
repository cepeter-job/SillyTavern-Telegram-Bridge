from __future__ import annotations

import subprocess
from types import SimpleNamespace

from settings_test_support import make_test_settings

SENSITIVE = {
    "SILLYTAVERN_TELEGRAM_BOT_TOKEN": "bot-secret",
    "LLM_API_KEY": "provider-secret",
    "HINDSIGHT_API_KEY": "memory-secret",
    "SILLYTAVERN_SYNC_API_PASSWORD": "sync-secret",
}


def poison_environment(monkeypatch):
    for key, value in SENSITIVE.items():
        monkeypatch.setenv(key, value)


def assert_scrubbed(kwargs):
    env = kwargs.get("env")
    assert isinstance(env, dict)
    for key in SENSITIVE:
        assert key not in env


def test_pdf_worker_does_not_inherit_bridge_secrets(tmp_path, monkeypatch):
    import bridge.document_extraction as extraction

    poison_environment(monkeypatch)
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(stdout=b'{"ok":true,"text":"fixture"}')

    monkeypatch.setattr(extraction.subprocess, "run", run)
    assert (
        extraction.extract_pdf_data_bank_text(b"fixture", app_settings=make_test_settings(home=tmp_path)) == "fixture"
    )
    assert_scrubbed(calls[0][1])


def test_tts_helpers_do_not_inherit_bridge_secrets(tmp_path, monkeypatch):
    import bridge.speech as speech

    poison_environment(monkeypatch)
    calls = []
    monkeypatch.setattr(speech, "_resolve_media_command", lambda configured, label: f"/usr/bin/{label.lower()}")

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(speech.subprocess, "run", run)
    settings = make_test_settings(home=tmp_path, environ={"SILLYTAVERN_TTS_VOICE": "fixture"})
    speech.synthesize_voice("hello", tmp_path / "voice.ogg", app_settings=settings)
    assert len(calls) == 2
    for _argv, kwargs in calls:
        assert_scrubbed(kwargs)


def test_tailscale_cli_does_not_inherit_bridge_secrets(monkeypatch):
    import bridge.tailscale_funnel as funnel

    poison_environment(monkeypatch)
    calls = []
    monkeypatch.setattr(funnel.shutil, "which", lambda name: "/usr/bin/tailscale")

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "1.102.4\n", "")

    monkeypatch.setattr(funnel.subprocess, "run", run)
    assert funnel._tailscale("version") == "1.102.4\n"
    assert_scrubbed(calls[0][1])


def test_runtime_health_git_does_not_inherit_bridge_secrets(tmp_path, monkeypatch):
    import bridge.runtime_health as health

    poison_environment(monkeypatch)
    root = tmp_path / "source"
    (root / "bridge").mkdir(parents=True)
    (root / "CHANGELOG.md").write_text("## [0.2.099]\n", encoding="utf-8")
    from dataclasses import replace

    settings = replace(make_test_settings(home=tmp_path), update_repo_dir=root)
    monkeypatch.setattr(health, "__file__", str(root / "bridge/runtime_health.py"))
    monkeypatch.setattr(health.shutil, "which", lambda name: "/usr/bin/git")
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        stdout = "" if "status" in argv else "a" * 40 + "\n"
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    monkeypatch.setattr(health.subprocess, "run", run)
    assert health.capture_deployment(settings).commit == "a" * 40
    assert len(calls) == 2
    for _argv, kwargs in calls:
        assert_scrubbed(kwargs)


def test_self_update_supervisor_commands_do_not_inherit_bridge_secrets(monkeypatch):
    import bridge.self_update as update

    poison_environment(monkeypatch)
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(update.subprocess, "run", run)
    update._run(["/usr/bin/true"])
    assert_scrubbed(calls[0][1])
