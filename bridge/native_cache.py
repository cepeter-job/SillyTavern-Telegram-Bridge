"""Bounded, copy-isolated caches for unchanged native SillyTavern files."""

from __future__ import annotations

import copy
import json
import sys
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator
from itertools import chain
from pathlib import Path
from typing import Any, NamedTuple, cast

_NATIVE_CACHE_LOCK = threading.RLock()
_NATIVE_CACHE_MAX_ENTRIES = 128
# Bound both source size and the decoded object graph (not process RSS).
_NATIVE_CACHE_MAX_SOURCE_BYTES = 4 * 1024 * 1024
_NATIVE_CACHE_MAX_HEAP_BYTES = 4 * 1024 * 1024
_TEXT_CACHE_MAX_ENTRIES = 128
_TEXT_CACHE_MAX_BYTES = 1024 * 1024


class _NativeEntry(NamedTuple):
    value: object
    heap_bytes: int


_NATIVE_CACHE: OrderedDict[tuple[str, str, int, int], _NativeEntry] = OrderedDict()
_TEXT_CACHE: OrderedDict[str, str] = OrderedDict()


def _key(path: Path) -> tuple[str, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (str(path.resolve()), int(stat.st_mtime_ns), int(stat.st_size))


def _retained_heap_size(value: object, limit: int) -> int:
    """Bounded, cycle-safe estimate for decoded native data and its cache key.

    Unsupported object types are not cached: their referenced memory cannot be
    accounted for here. Iterator frames avoid copying wide lists during sizing.
    """
    pending: list[Iterator[object]] = [iter((value,))]
    seen: set[int] = set()
    total = 0
    while pending:
        try:
            current = next(pending[-1])
        except StopIteration:
            pending.pop()
            continue
        identity = id(current)
        if identity in seen:
            continue
        total += sys.getsizeof(current)
        if total > limit:
            return total
        seen.add(identity)
        kind = type(current)
        if kind is dict:
            mapping = cast(dict[object, object], current)
            pending.append(chain(mapping.keys(), mapping.values()))
        elif kind in (list, tuple, set, frozenset):
            pending.append(iter(cast(Iterable[object], current)))
        elif kind not in (str, bytes, int, float, bool, type(None)):
            return limit + 1
    return total


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
        entry = _NATIVE_CACHE.get(key)
        if entry is not None:
            _NATIVE_CACHE.move_to_end(key)
    if entry is not None:
        return copy.deepcopy(entry.value)

    value = loader(path)
    heap_bytes = _NATIVE_CACHE_MAX_HEAP_BYTES + 1
    if key[3] <= _NATIVE_CACHE_MAX_SOURCE_BYTES:
        heap_bytes = _retained_heap_size((key, value), _NATIVE_CACHE_MAX_HEAP_BYTES)
    retained = False
    with _NATIVE_CACHE_LOCK:
        # A concurrent edit must not install an already-stale revision.
        if _key(path) == revision:
            for previous in tuple(_NATIVE_CACHE):
                if previous[:2] == key[:2]:
                    del _NATIVE_CACHE[previous]
            if heap_bytes <= _NATIVE_CACHE_MAX_HEAP_BYTES:
                _NATIVE_CACHE[key] = _NativeEntry(value, heap_bytes)
                while _NATIVE_CACHE and (
                    len(_NATIVE_CACHE) > _NATIVE_CACHE_MAX_ENTRIES
                    or sum(entry[3] for entry in _NATIVE_CACHE) > _NATIVE_CACHE_MAX_SOURCE_BYTES
                    or sum(entry.heap_bytes for entry in _NATIVE_CACHE.values()) > _NATIVE_CACHE_MAX_HEAP_BYTES
                ):
                    _NATIVE_CACHE.popitem(last=False)
                retained = key in _NATIVE_CACHE
    # Only a retained cache value needs copy isolation. Large/stale misses belong
    # to their caller already; copying them used to double the decoded data peak.
    return copy.deepcopy(value) if retained else value


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
