"""Cache retention is bounded without changing parsed content or copy isolation."""

import json
import os
from collections import OrderedDict

import pytest

from bridge import native_cache


@pytest.fixture(autouse=True)
def isolated_caches(monkeypatch):
    monkeypatch.setattr(native_cache, "_NATIVE_CACHE", OrderedDict())
    monkeypatch.setattr(native_cache, "_TEXT_CACHE", OrderedDict())


def write_revision(path, revision):
    path.write_text(json.dumps({"revision": revision, "nested": {"value": "original"}}), encoding="utf-8")
    stamp = 1_800_000_000_000_000_000 + revision
    os.utime(path, ns=(stamp, stamp))


def load_metadata(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(params=["json", "png-metadata"])
def load_native(request):
    if request.param == "json":
        return native_cache.cached_json
    return lambda path: native_cache.cached_png_metadata(path, load_metadata)


def test_file_edits_do_not_retain_historical_revisions(tmp_path, load_native):
    path = tmp_path / "native.card"
    for revision in range(100):
        write_revision(path, revision)
        assert load_native(path)["revision"] == revision
    assert len(native_cache._NATIVE_CACHE) == 1


def test_cached_content_is_an_independent_deep_copy(tmp_path, load_native):
    path = tmp_path / "native.card"
    write_revision(path, 1)
    first = load_native(path)
    first["nested"]["value"] = "changed"
    assert load_native(path)["nested"]["value"] == "original"


def test_missing_file_releases_its_cached_revision(tmp_path, load_native):
    path = tmp_path / "native.card"
    write_revision(path, 1)
    load_native(path)
    path.unlink()
    with pytest.raises(OSError):
        load_native(path)
    assert len(native_cache._NATIVE_CACHE) == 0


def test_native_cache_evicts_least_recently_used_file(tmp_path, monkeypatch):
    monkeypatch.setattr(native_cache, "_NATIVE_CACHE_MAX_ENTRIES", 2, raising=False)
    paths = [tmp_path / name for name in ("a", "b", "c")]
    for path in paths:
        write_revision(path, 1)
    loaded = []

    def loader(path):
        loaded.append(path.name)
        return load_metadata(path)

    for index in (0, 1, 0, 2, 0, 1):
        native_cache.cached_png_metadata(paths[index], loader)
    assert loaded == ["a", "b", "c", "b"]
    assert len(native_cache._NATIVE_CACHE) == 2


def test_native_cache_obeys_total_source_byte_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(native_cache, "_NATIVE_CACHE_MAX_SOURCE_BYTES", 80, raising=False)
    for name in ("a", "b"):
        path = tmp_path / name
        write_revision(path, 1)
        native_cache.cached_json(path)
    assert len(native_cache._NATIVE_CACHE) == 1


def test_oversized_native_file_is_read_but_not_retained(tmp_path, monkeypatch):
    monkeypatch.setattr(native_cache, "_NATIVE_CACHE_MAX_SOURCE_BYTES", 10, raising=False)
    path = tmp_path / "large.json"
    write_revision(path, 1)
    assert native_cache.cached_json(path)["revision"] == 1
    assert len(native_cache._NATIVE_CACHE) == 0


def test_revision_changed_during_load_is_not_cached(tmp_path):
    path = tmp_path / "changing.card"
    write_revision(path, 1)

    def loader(current):
        value = load_metadata(current)
        write_revision(current, 2)
        return value

    assert native_cache.cached_png_metadata(path, loader)["revision"] == 1
    assert len(native_cache._NATIVE_CACHE) == 0
    assert native_cache.cached_png_metadata(path, load_metadata)["revision"] == 2


def test_loader_kinds_cannot_share_incompatible_cached_values(tmp_path):
    path = tmp_path / "native.card"
    write_revision(path, 1)
    assert native_cache.cached_json(path)["revision"] == 1
    assert native_cache.cached_png_metadata(path, lambda _: {"parsed": "metadata"}) == {"parsed": "metadata"}


def test_text_cache_evicts_least_recently_used_entry(monkeypatch):
    monkeypatch.setattr(native_cache, "_TEXT_CACHE_MAX_ENTRIES", 2, raising=False)
    assert native_cache.cached_text("a", lambda: "A") == "A"
    native_cache.cached_text("b", lambda: "B")
    assert native_cache.cached_text("a", lambda: "wrong") == "A"
    native_cache.cached_text("c", lambda: "C")
    assert native_cache.cached_text("b", lambda: "rebuilt") == "rebuilt"
    assert len(native_cache._TEXT_CACHE) == 2


def test_text_cache_byte_budget_accounts_for_utf8(monkeypatch):
    monkeypatch.setattr(native_cache, "_TEXT_CACHE_MAX_BYTES", 40, raising=False)
    for key in ("a", "b"):
        assert native_cache.cached_text(key, lambda: "é" * 16) == "é" * 16
    assert len(native_cache._TEXT_CACHE) == 1


@pytest.mark.parametrize("key,value", [("key", "x" * 50), ("k" * 50, "value")])
def test_oversized_text_value_or_key_is_not_retained(monkeypatch, key, value):
    monkeypatch.setattr(native_cache, "_TEXT_CACHE_MAX_BYTES", 40, raising=False)
    assert native_cache.cached_text(key, lambda: value) == value
    assert len(native_cache._TEXT_CACHE) == 0
