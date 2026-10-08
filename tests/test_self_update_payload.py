"""A current signed marker must not hide drift in managed release files."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from test_self_update_security import engine as engine
from test_self_update_security import fake_supervisor, git, make_plan
from test_self_update_security import release_tree as release_tree


@pytest.mark.parametrize("drift", ["modified", "missing", "obsolete_module", "missing_launcher"])
def test_current_signed_release_repairs_managed_payload_drift(engine, release_tree, monkeypatch, drift):
    data = release_tree
    fake_supervisor(monkeypatch, engine)
    plan = make_plan(engine, data)
    assert engine.apply_update(plan).status is engine.UpdateStatus.RESTART_SCHEDULED
    marker = (data.live / engine.MARKER).read_bytes()
    note = data.live / "operator-note.txt"
    note.write_text("Preserve my deployment note.\n")
    module = data.live / "bridge/feature.py"
    if drift == "modified":
        original = module.stat()
        module.write_text("VALUE = 3\n")  # Equal size and timestamp still require content comparison.
        os.utime(module, ns=(original.st_atime_ns, original.st_mtime_ns))
    elif drift == "missing":
        module.unlink()
    elif drift == "obsolete_module":
        (data.live / "bridge/retired.py").write_text("VALUE = 'stale module'\n")
    else:
        (data.live / "sillytavern_telegram_bridge.py").unlink()

    result = engine.apply_update(plan)

    assert result.status is engine.UpdateStatus.RESTART_SCHEDULED
    assert result.source_changed is False and result.live_changed is True
    assert git(data.source, "rev-parse", "HEAD") == data.new
    assert module.read_text() == "VALUE = 2\n"
    assert (data.live / "sillytavern_telegram_bridge.py").is_file()
    assert not (data.live / "bridge/retired.py").exists()
    assert (data.live / engine.MARKER).read_bytes() == marker
    preserved = list(data.live.parent.glob(".bridge-previous-*/operator-note.txt"))
    assert len(preserved) == 1 and preserved[0].read_text() == "Preserve my deployment note.\n"


def test_current_signed_payload_ignores_generated_cache_and_unmanaged_notes(engine, release_tree, monkeypatch):
    data = release_tree
    calls = fake_supervisor(monkeypatch, engine)
    plan = make_plan(engine, data)
    assert engine.apply_update(plan).status is engine.UpdateStatus.RESTART_SCHEDULED
    assert list((data.live / "bridge/__pycache__").glob("*.pyc"))
    note = data.live / "operator-note.txt"
    note.write_text("Keep in place.\n")
    restarts = sum(Path(call[0]).name == "systemd-run" for call in calls)

    result = engine.apply_update(plan)

    assert result.status is engine.UpdateStatus.ALREADY_LATEST
    assert not result.source_changed and not result.live_changed
    assert sum(Path(call[0]).name == "systemd-run" for call in calls) == restarts
    assert note.read_text() == "Keep in place.\n"


def test_current_release_refuses_nested_symlink_without_touching_its_target(engine, release_tree, monkeypatch):
    data = release_tree
    fake_supervisor(monkeypatch, engine)
    plan = make_plan(engine, data)
    assert engine.apply_update(plan).status is engine.UpdateStatus.RESTART_SCHEDULED
    external = data.live.parent / "operator-file.txt"
    external.write_text("Private operator data.\n")
    module = data.live / "bridge/feature.py"
    module.unlink()
    module.symlink_to(external)

    result = engine.apply_update(plan)

    assert result.status is engine.UpdateStatus.REFUSED and result.code == "target"
    assert module.is_symlink()
    assert external.read_text() == "Private operator data.\n"
