"""Speech models retain only the latest idle model and release it once it goes idle."""

import gc
import sys
import threading
import weakref
from types import SimpleNamespace

import pytest

from bridge import speech


def _stop_idle_timer() -> None:
    timer = speech._STT_IDLE_TIMER
    if timer is not None:
        timer.cancel()
    speech._STT_IDLE_TIMER = None


@pytest.fixture
def fake_models(monkeypatch):
    loaded = []
    references = {}

    class FakeWhisper:
        def __init__(self, name, **kwargs):
            self.name = name
            loaded.append(name)
            references[name] = weakref.ref(self)

        def transcribe(self, filename, **kwargs):
            return iter([SimpleNamespace(text=self.name)]), None

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=FakeWhisper))
    monkeypatch.setattr(speech, "_STT_MODEL_CACHE", {})
    _stop_idle_timer()
    yield SimpleNamespace(loaded=loaded, references=references, model=FakeWhisper, settings=SimpleNamespace(environ={}))
    _stop_idle_timer()


def transcribe(fake_models, name):
    return speech.transcribe_audio_bytes(b"fixture", model_name=name, app_settings=fake_models.settings)


def test_repeated_transcription_reuses_the_current_model(fake_models):
    assert transcribe(fake_models, "base") == "base"
    assert transcribe(fake_models, "base") == "base"
    assert fake_models.loaded == ["base"]


def test_model_switches_do_not_retain_idle_models(fake_models):
    for name in ("base", "small", "medium"):
        assert transcribe(fake_models, name) == name
    gc.collect()
    assert list(speech._STT_MODEL_CACHE) == ["medium"]
    assert fake_models.references["base"]() is None
    assert fake_models.references["small"]() is None


def test_eviction_preserves_an_inflight_transcription(fake_models, monkeypatch):
    entered = threading.Event()
    finish = threading.Event()
    results = []
    errors = []

    def fake_transcribe(model, filename, **kwargs):
        if model.name == "base":
            entered.set()
            if not finish.wait(5):
                raise RuntimeError("test release timed out")
        return iter([SimpleNamespace(text=model.name)]), None

    def worker():
        try:
            results.append(transcribe(fake_models, "base"))
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(fake_models.model, "transcribe", fake_transcribe)
    thread = threading.Thread(target=worker)
    try:
        thread.start()
        assert entered.wait(5)
        assert transcribe(fake_models, "small") == "small"
        gc.collect()
        assert fake_models.references["base"]() is not None
    finally:
        finish.set()
        thread.join(5)
    assert not thread.is_alive()
    assert not errors
    assert results == ["base"]
    gc.collect()
    assert fake_models.references["base"]() is None


def test_idle_model_is_released_once_its_window_elapses(fake_models):
    assert transcribe(fake_models, "base") == "base"
    assert list(speech._STT_MODEL_CACHE) == ["base"]
    speech._release_idle_model(now=speech.time.monotonic() + speech.STT_IDLE_RELEASE_SECONDS)
    gc.collect()
    assert speech._STT_MODEL_CACHE == {}
    assert fake_models.references["base"]() is None


def test_idle_release_spares_a_model_still_inside_its_window(fake_models):
    assert transcribe(fake_models, "base") == "base"
    speech._release_idle_model(now=speech.time.monotonic() + speech.STT_IDLE_RELEASE_SECONDS - 1)
    assert list(speech._STT_MODEL_CACHE) == ["base"]
    assert fake_models.references["base"]() is not None


def test_idle_release_preserves_an_inflight_transcription(fake_models, monkeypatch):
    entered = threading.Event()
    finish = threading.Event()
    results = []
    errors = []

    def fake_transcribe(model, filename, **kwargs):
        entered.set()
        if not finish.wait(5):
            raise RuntimeError("test release timed out")
        return iter([SimpleNamespace(text=model.name)]), None

    def worker():
        try:
            results.append(transcribe(fake_models, "base"))
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(fake_models.model, "transcribe", fake_transcribe)
    thread = threading.Thread(target=worker)
    try:
        thread.start()
        assert entered.wait(5)
        speech._release_idle_model(now=speech.time.monotonic() + speech.STT_IDLE_RELEASE_SECONDS)
        assert speech._STT_MODEL_CACHE == {}
        gc.collect()
        assert fake_models.references["base"]() is not None
    finally:
        finish.set()
        thread.join(5)
    assert not thread.is_alive()
    assert not errors
    assert results == ["base"]
    gc.collect()
    assert fake_models.references["base"]() is None
