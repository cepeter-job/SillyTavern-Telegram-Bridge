"""Signed updates honor the dependency lock for the active interpreter."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_self_update_security import engine as engine
from test_self_update_security import fake_supervisor, git, make_plan, managed_mirror
from test_self_update_security import release_tree as release_tree


def _runtime(monkeypatch, engine, version):
    monkeypatch.setattr(engine, "sys", SimpleNamespace(executable=engine.sys.executable, version_info=version))


def _files(root):
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("runtime", [(3, 11), (3, 14)])
@pytest.mark.parametrize(
    ("release_tree", "release_lock"),
    [(True, "changed"), (True, None), (False, "present"), (False, None)],
    indirect=["release_tree"],
    ids=["drift", "missing-release", "missing-source", "missing-both"],
)
def test_signed_update_checks_active_runtime_lock_before_backup_and_activation(
    engine, release_tree, monkeypatch, runtime, release_lock
):
    data = release_tree
    lock = data.remote / "requirements-py314.lock"
    if release_lock is None:
        lock.unlink(missing_ok=True)
    else:
        lock.write_text(f"# {release_lock} Python 3.14 lock\n")
    if git(data.remote, "status", "--porcelain"):
        git(data.remote, "add", ".")
        git(data.remote, "commit", "-m", "runtime lock fixture")
    git(data.remote, "tag", "-d", "v0.0.2")
    git(
        data.remote, "-c", "gpg.format=ssh", "-c", f"user.signingkey={data.key}", "tag", "-s", "v0.0.2", "-m", "release"
    )
    managed_mirror(engine, data)
    before = _files(data.live)
    calls = fake_supervisor(monkeypatch, engine)
    _runtime(monkeypatch, engine, runtime)
    backups = []
    monkeypatch.setattr(engine, "create_database_backup", lambda *a, **k: backups.append("backup"))
    plan = replace(
        make_plan(engine, data),
        database=data.live.parent / "bridge.sqlite3",
        database_backup_dir=data.live.parent / "backups",
    )

    result = engine.apply_update(plan)

    if runtime == (3, 14):
        assert result.status is engine.UpdateStatus.REFUSED
        assert result.code == "dependencies"
        assert not result.source_changed and not result.live_changed
        assert git(data.source, "rev-parse", "HEAD") == data.old
        assert git(data.source, "status", "--porcelain") == ""
        assert _files(data.live) == before
        assert backups == []
        assert not any(Path(call[0]).name == "systemd-run" for call in calls)
    else:
        assert result.status is engine.UpdateStatus.RESTART_SCHEDULED
        assert git(data.source, "rev-parse", "HEAD") == git(data.remote, "rev-parse", "HEAD")
        assert (data.live / "bridge/feature.py").read_text() == "VALUE = 2\n"
        assert backups == ["backup"]


@pytest.mark.parametrize("release_tree", [False], indirect=True)
def test_python314_requires_runtime_lock_even_for_current_verified_release(engine, release_tree, monkeypatch):
    data = release_tree
    fake_supervisor(monkeypatch, engine)
    plan = make_plan(engine, data)
    _runtime(monkeypatch, engine, (3, 11))
    assert engine.apply_update(plan).status is engine.UpdateStatus.RESTART_SCHEDULED
    before = _files(data.live)
    _runtime(monkeypatch, engine, (3, 14))

    result = engine.apply_update(plan)

    assert result.status is engine.UpdateStatus.REFUSED
    assert result.code == "dependencies"
    assert not result.source_changed and not result.live_changed
    assert git(data.source, "rev-parse", "HEAD") == data.new
    assert _files(data.live) == before
