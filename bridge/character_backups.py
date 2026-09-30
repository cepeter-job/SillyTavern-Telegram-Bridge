"""Pure character-backup discovery helpers."""

from __future__ import annotations

import re
from pathlib import Path

from bridge.limits import RAG_MAX_FILE_BYTES
from bridge.settings import AppSettings

_VERSIONED_CHARACTER_BACKUP = re.compile(r"^.+\.\d{15,}\.png$", re.IGNORECASE)


def character_restore_targets(*, app_settings: AppSettings) -> list[str]:
    backup_dir = app_settings.character_backup_dir
    if not backup_dir.exists():
        return []
    targets: list[str] = []
    for path in backup_dir.glob("*.png"):
        if path.is_symlink() or not path.is_file():
            continue
        if path.parent.resolve() != backup_dir.resolve():
            continue
        if _VERSIONED_CHARACTER_BACKUP.match(path.name):
            continue
        if path.stat().st_size > RAG_MAX_FILE_BYTES:
            continue
        if Path(path.name).name != path.name or path.name.startswith("."):
            continue
        targets.append(path.name)
    return sorted(set(targets), key=str.casefold)
