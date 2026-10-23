"""Compare release locks required by the interpreter running an update."""

from __future__ import annotations

import hashlib
from pathlib import Path


def runtime_dependency_locks_match(source: Path, release: Path, runtime_version: tuple[int, ...]) -> bool:
    names: tuple[str, ...] = ("requirements.lock",)
    if runtime_version == (3, 14):
        names += ("requirements-py314.lock",)
    try:
        for name in names:
            installed, candidate = source / name, release / name
            if any(path.is_symlink() or not path.is_file() for path in (installed, candidate)):
                return False
            if hashlib.sha256(installed.read_bytes()).digest() != hashlib.sha256(candidate.read_bytes()).digest():
                return False
    except OSError:
        return False
    return True
