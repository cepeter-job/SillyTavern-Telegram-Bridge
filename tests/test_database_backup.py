from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest


def make_database(path: Path, value: str) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS sample(value TEXT NOT NULL)")
    db.execute("DELETE FROM sample")
    db.execute("INSERT INTO sample(value) VALUES(?)", (value,))
    db.commit()
    return db


def read_value(path: Path) -> str:
    with sqlite3.connect(path) as db:
        return str(db.execute("SELECT value FROM sample").fetchone()[0])


def test_online_backup_captures_committed_wal_database_and_is_private(tmp_path):
    from bridge.database_backup import create_database_backup

    source = tmp_path / "live.sqlite3"
    live = make_database(source, "before")
    try:
        backup = create_database_backup(source, tmp_path / "backups", label="pre-update")
        live.execute("UPDATE sample SET value='after'")
        live.commit()
    finally:
        live.close()

    assert read_value(backup) == "before"
    assert backup.parent == tmp_path / "backups"
    if os.name == "posix":
        assert backup.stat().st_mode & 0o077 == 0
        assert backup.parent.stat().st_mode & 0o077 == 0


def test_restore_refuses_while_service_is_active(tmp_path):
    from bridge.database_backup import restore_database_backup

    backup = tmp_path / "backup.sqlite3"
    make_database(backup, "backup").close()
    target = tmp_path / "live.sqlite3"
    make_database(target, "current").close()

    with pytest.raises(RuntimeError, match=r"service.*active"):
        restore_database_backup(
            backup,
            target,
            tmp_path / "restore-backups",
            service_name="bridge.service",
            service_active=lambda _service: True,
        )
    assert read_value(target) == "current"


def test_restore_validates_backup_and_preserves_pre_restore_snapshot(tmp_path):
    from bridge.database_backup import restore_database_backup

    backup = tmp_path / "backup.sqlite3"
    make_database(backup, "wanted").close()
    target = tmp_path / "live.sqlite3"
    make_database(target, "current").close()
    (tmp_path / "live.sqlite3-wal").write_bytes(b"stale")
    (tmp_path / "live.sqlite3-shm").write_bytes(b"stale")

    previous = restore_database_backup(
        backup,
        target,
        tmp_path / "restore-backups",
        service_name="bridge.service",
        service_active=lambda _service: False,
    )

    assert not (tmp_path / "live.sqlite3-wal").exists()
    assert not (tmp_path / "live.sqlite3-shm").exists()
    assert read_value(target) == "wanted"
    assert previous is not None and read_value(previous) == "current"


def test_restore_rejects_corrupt_backup_without_touching_database(tmp_path):
    from bridge.database_backup import restore_database_backup

    backup = tmp_path / "bad.sqlite3"
    backup.write_bytes(b"not sqlite")
    target = tmp_path / "live.sqlite3"
    make_database(target, "current").close()

    with pytest.raises(RuntimeError, match="valid SQLite backup"):
        restore_database_backup(
            backup,
            target,
            tmp_path / "restore-backups",
            service_name="bridge.service",
            service_active=lambda _service: False,
        )
    assert read_value(target) == "current"
