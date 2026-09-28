"""Verified online SQLite snapshots and guarded offline restore."""

from __future__ import annotations

import os
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from bridge.subprocess_security import minimal_subprocess_environment

_BACKUP_KEEP = 10


def _regular_file(path: Path, *, label: str) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise RuntimeError(f"{label} is unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise RuntimeError(f"{label} must be a regular file")


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError("database backup directory must be a regular directory")
    info = path.stat()
    if os.name == "posix":
        if info.st_uid != os.geteuid():
            raise RuntimeError("database backup directory owner is invalid")
        path.chmod(0o700)


def _readonly_connection(path: Path) -> sqlite3.Connection:
    uri = path.resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=10)


def _quick_check(db: sqlite3.Connection) -> None:
    try:
        result = db.execute("PRAGMA quick_check").fetchone()
    except sqlite3.Error as exc:
        raise RuntimeError("database is not a valid SQLite backup") from exc
    if not result or str(result[0]).casefold() != "ok":
        raise RuntimeError("database is not a valid SQLite backup")


def _safe_label(value: str) -> str:
    label = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(value)).strip("-_")
    return label[:40] or "backup"


def _backup_name(source: Path, label: str) -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    nonce = f"{time.time_ns() % 1_000_000_000:09d}"
    return f"{source.stem}-{_safe_label(label)}-{stamp}-{nonce}.sqlite3"


def _prune_backups(directory: Path, source_stem: str, keep: int) -> None:
    candidates = sorted(
        (path for path in directory.glob(f"{source_stem}-*.sqlite3") if path.is_file() and not path.is_symlink()),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    for stale in candidates[max(1, int(keep)) :]:
        stale.unlink()


def create_database_backup(
    database: Path,
    backup_dir: Path,
    *,
    label: str = "manual",
    keep: int = _BACKUP_KEEP,
) -> Path:
    """Snapshot one live SQLite database using SQLite's online backup API."""
    source = Path(database)
    _regular_file(source, label="database")
    directory = Path(backup_dir)
    _private_directory(directory)
    destination = directory / _backup_name(source, label)
    if destination.exists() or destination.is_symlink():
        raise RuntimeError("database backup destination already exists")
    try:
        with _readonly_connection(source) as input_db, sqlite3.connect(destination, timeout=10) as output_db:
            input_db.backup(output_db, pages=256, sleep=0.01)
            _quick_check(output_db)
        if os.name == "posix":
            destination.chmod(0o600)
        _prune_backups(directory, source.stem, keep)
        return destination
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def _default_service_active(service_name: str) -> bool:
    executable = shutil.which("systemctl")
    if not executable:
        raise RuntimeError("cannot verify that the bridge service is stopped")
    result = subprocess.run(  # noqa: S603 -- fixed systemctl query, no shell
        [executable, "--user", "is-active", "--quiet", service_name],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
        env=minimal_subprocess_environment(),
    )
    if result.returncode == 0:
        return True
    if result.returncode in {3, 4}:
        return False
    raise RuntimeError("cannot verify that the bridge service is stopped")


def restore_database_backup(
    backup: Path,
    database: Path,
    backup_dir: Path,
    *,
    service_name: str,
    service_active: Callable[[str], bool] = _default_service_active,
) -> Path | None:
    """Restore a verified SQLite snapshot only while the user service is inactive."""
    source = Path(backup)
    target = Path(database)
    _regular_file(source, label="database backup")
    if service_active(service_name):
        raise RuntimeError(f"bridge service {service_name} is active; stop it before restoring the database")
    with _readonly_connection(source) as backup_db:
        _quick_check(backup_db)

    previous: Path | None = None
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if target.exists() or target.is_symlink():
        _regular_file(target, label="database")
        previous = create_database_backup(target, backup_dir, label="pre-restore")

    descriptor, raw_temp = tempfile.mkstemp(prefix=f".{target.name}.restore-", dir=target.parent)
    os.close(descriptor)
    temporary = Path(raw_temp)
    try:
        with _readonly_connection(source) as input_db, sqlite3.connect(temporary, timeout=10) as output_db:
            input_db.backup(output_db, pages=256, sleep=0.01)
            _quick_check(output_db)
        if os.name == "posix":
            temporary.chmod(0o600)
        for suffix in ("-wal", "-shm"):
            Path(str(target) + suffix).unlink(missing_ok=True)
        os.replace(temporary, target)
        return previous
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
