"""Backup handles must close before return, unlink or restore activation."""

from __future__ import annotations

import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest


def wal_database(path, value="committed"):
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA wal_autocheckpoint=0")
    db.execute("CREATE TABLE sample(value TEXT)")
    db.execute("INSERT INTO sample VALUES(?)", (value,))
    db.commit()
    return db


def track_connections(monkeypatch):
    import bridge.database_backup as module

    real_connect = sqlite3.connect
    connections = []

    def connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connections.append(connection)  # Hold refs: GC must not mask missing close().
        return connection

    monkeypatch.setattr(module.sqlite3, "connect", connect)
    return connections


def assert_closed(connections):
    assert len(connections) >= 2
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


@pytest.mark.parametrize("invalid", [False, True])
def test_backup_closes_real_connections_on_success_and_failure(tmp_path, monkeypatch, invalid):
    import bridge.database_backup as module

    source = tmp_path / "live.sqlite3"
    with closing(wal_database(source)):
        connections = track_connections(monkeypatch)
        if invalid:

            def reject(_db):
                raise RuntimeError("injected validation failure")

            monkeypatch.setattr(module, "_quick_check", reject)
        try:
            if invalid:
                with pytest.raises(RuntimeError, match="injected"):
                    module.create_database_backup(source, tmp_path / "backups")
                assert not list((tmp_path / "backups").iterdir())
            else:
                result = module.create_database_backup(source, tmp_path / "backups")
                assert result.is_file()
            assert_closed(connections)
        finally:
            for db in connections:
                db.close()


def test_backup_is_a_standalone_file_without_changing_live_wal_mode(tmp_path):
    from bridge.database_backup import create_database_backup

    source = tmp_path / "live.sqlite3"
    with closing(wal_database(source)) as live:
        assert Path(str(source) + "-wal").stat().st_size > 0
        backup = create_database_backup(source, tmp_path / "backups")
        assert sorted(p.name for p in backup.parent.iterdir()) == [backup.name]
        with closing(sqlite3.connect(backup)) as db:
            assert db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert live.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        live.execute("UPDATE sample SET value='later'")
        live.commit()
        single = tmp_path / "copied-alone.sqlite3"
        shutil.copyfile(backup, single)
        with closing(sqlite3.connect(single.as_uri() + "?immutable=1", uri=True)) as db:
            assert db.execute("SELECT value FROM sample").fetchone() == ("committed",)


@pytest.mark.parametrize("activation_fails", [False, True])
def test_restore_closes_handles_and_removes_temporary_sidecars(tmp_path, monkeypatch, activation_fails):
    import bridge.database_backup as module

    source, target = tmp_path / "backup.sqlite3", tmp_path / "live.sqlite3"
    wal_database(source, "wanted").close()
    wal_database(target, "original").close()
    connections = track_connections(monkeypatch)
    if activation_fails:

        def reject(_source, _target):
            raise OSError("injected activation failure")

        monkeypatch.setattr(module.os, "replace", reject)
    try:
        kwargs = dict(service_name="fixture.service", service_active=lambda _: False)
        if activation_fails:
            with pytest.raises(OSError, match="injected activation"):
                module.restore_database_backup(source, target, tmp_path / "saved", **kwargs)
        else:
            module.restore_database_backup(source, target, tmp_path / "saved", **kwargs)
        assert_closed(connections)
        assert not list(tmp_path.glob(".live.sqlite3.restore-*"))
    finally:
        for db in connections:
            db.close()
    with closing(sqlite3.connect(target)) as db:
        assert db.execute("SELECT value FROM sample").fetchone()[0] == ("original" if activation_fails else "wanted")
