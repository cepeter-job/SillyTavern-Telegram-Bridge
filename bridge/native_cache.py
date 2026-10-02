"""Bounded, copy-isolated caches for unchanged native SillyTavern files."""

from __future__ import annotations

import copy
import json
import threading
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

_NATIVE_CACHE_LOCK = threading.RLock()
_NATIVE_CACHE_MAX_ENTRIES = 128
# Source bytes bound retained input size, not the parsed Python heap footprint.
_NATIVE_CACHE_MAX_SOURCE_BYTES = 4 * 1024 * 1024
_TEXT_CACHE_MAX_ENTRIES = 128
_TEXT_CACHE_MAX_BYTES = 1024 * 1024
_NATIVE_CACHE: OrderedDict[tuple[str, str, int, int], object] = OrderedDict()
_TEXT_CACHE: OrderedDict[str, str] = OrderedDict()
_MISSING = object()


def _key(path: Path) -> tuple[str, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (str(path.resolve()), int(stat.st_mtime_ns), int(stat.st_size))


def _cached_native(path: Path, kind: str, loader: Callable[[Path], object]) -> object:
    revision = _key(path)
    if revision is None:
        resolved = str(path.resolve())
        with _NATIVE_CACHE_LOCK:
            for previous in tuple(_NATIVE_CACHE):
                if previous[1] == resolved:
                    del _NATIVE_CACHE[previous]
        raise OSError(f"native file is unavailable: {path}")
    key = (kind, *revision)
    with _NATIVE_CACHE_LOCK:
        value = _NATIVE_CACHE.get(key, _MISSING)
        if value is not _MISSING:
            _NATIVE_CACHE.move_to_end(key)
    if value is _MISSING:
        value = loader(path)
        with _NATIVE_CACHE_LOCK:
            # A concurrent edit must not install an already-stale revision.
            if _key(path) == revision:
                for previous in tuple(_NATIVE_CACHE):
                    if previous[:2] == key[:2]:
                        del _NATIVE_CACHE[previous]
                if key[3] <= _NATIVE_CACHE_MAX_SOURCE_BYTES:
                    _NATIVE_CACHE[key] = value
                    while _NATIVE_CACHE and (
                        len(_NATIVE_CACHE) > _NATIVE_CACHE_MAX_ENTRIES
                        or sum(entry[3] for entry in _NATIVE_CACHE) > _NATIVE_CACHE_MAX_SOURCE_BYTES
                    ):
                        _NATIVE_CACHE.popitem(last=False)
    return copy.deepcopy(value)


def cached_json(path: Path) -> object:
    return _cached_native(path, "json", lambda current: json.loads(current.read_text(encoding="utf-8")))


def cached_png_metadata(path: Path, loader: Callable[[Path], dict[str, Any]]) -> dict[str, Any]:
    return cast(dict[str, Any], _cached_native(path, "png", loader))


def _text_size(key: str, value: str) -> int:
    return len(key.encode("utf-8")) + len(value.encode("utf-8"))


def cached_text(key: str, builder: Callable[[], object]) -> str:
    key = str(key)
    with _NATIVE_CACHE_LOCK:
        value = _TEXT_CACHE.get(key)
        if value is not None:
            _TEXT_CACHE.move_to_end(key)
    if value is None:
        value = str(builder())
        with _NATIVE_CACHE_LOCK:
            if _text_size(key, value) <= _TEXT_CACHE_MAX_BYTES:
                _TEXT_CACHE[key] = value
                _TEXT_CACHE.move_to_end(key)
                while _TEXT_CACHE and (
                    len(_TEXT_CACHE) > _TEXT_CACHE_MAX_ENTRIES
                    or sum(_text_size(k, v) for k, v in _TEXT_CACHE.items()) > _TEXT_CACHE_MAX_BYTES
                ):
                    _TEXT_CACHE.popitem(last=False)
    return value
