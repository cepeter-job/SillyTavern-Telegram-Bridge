"""Fail before native state or external I/O in affected memory runtime tests."""

import pytest

from bridge import codex_transport, memory_backend, npc_extraction, persona_sync, provider_transport, session_core


@pytest.fixture(autouse=True)
def isolated_memory_runtime(monkeypatch, tmp_path, request):
    def unexpected(*args, **kwargs):
        pytest.fail("Memory runtime test reached unconfigured native/external I/O")

    instance = getattr(request, "instance", None)
    if instance is not None and hasattr(instance, "app_settings_builder"):
        instance.app_settings_builder.home = tmp_path
    monkeypatch.setattr(persona_sync, "_native_settings", unexpected)
    monkeypatch.setattr(session_core, "default_persona_id", lambda **kwargs: "")
    monkeypatch.setattr(session_core, "_native_default_world", lambda **kwargs: "")
    monkeypatch.setattr(npc_extraction, "persona_name", lambda *args, **kwargs: "Synthetic User")
    monkeypatch.setattr(memory_backend, "hindsight_client", unexpected)
    monkeypatch.setattr(provider_transport, "strict_urlopen", unexpected)
    monkeypatch.setattr(codex_transport, "resolve_access_token", unexpected)
