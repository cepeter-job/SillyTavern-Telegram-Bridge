"""Combined shard reports must prove exact complete test coverage before merging coverage data."""

import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
from coverage import CoverageData

ROOT = Path(__file__).parents[1]


def manifests(tmp_path):
    inventory = [f"tests/test_{letter}.py::test_case" for letter in "abcd"]
    for index, nodeid in enumerate(inventory, 1):
        folder = tmp_path / f"pytest-shard-{index}"
        folder.mkdir()
        (folder / "shard-manifest.json").write_text(
            json.dumps(
                {
                    "shard_index": index,
                    "shard_count": 4,
                    "all_nodeids": inventory,
                    "selected_nodeids": [nodeid],
                }
            )
        )
    return tmp_path


def edit_manifest(root, index, change):
    path = root / f"pytest-shard-{index}" / "shard-manifest.json"
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))


def test_complete_disjoint_manifests_are_accepted(tmp_path):
    from tools.check_pytest_shards import check_manifests

    assert check_manifests(manifests(tmp_path), expected_shards=4) == 4


@pytest.mark.parametrize("fault", ["missing", "duplicate", "changed_inventory", "omitted", "extra", "file_split"])
def test_incomplete_or_overlapping_shards_fail_closed(tmp_path, fault):
    from tools.check_pytest_shards import check_manifests

    root = manifests(tmp_path)
    if fault == "missing":
        (root / "pytest-shard-4" / "shard-manifest.json").unlink()
    elif fault == "duplicate":
        edit_manifest(root, 4, lambda data: data.update(shard_index=3))
    elif fault == "changed_inventory":
        edit_manifest(root, 4, lambda data: data["all_nodeids"].append("tests/test_extra.py::test_extra"))
    elif fault == "omitted":
        edit_manifest(root, 4, lambda data: data.update(selected_nodeids=[]))
    elif fault == "extra":
        edit_manifest(root, 4, lambda data: data["selected_nodeids"].append("tests/test_extra.py::test_extra"))
    elif fault == "file_split":
        new = "tests/test_a.py::test_other"
        for index in range(1, 5):
            edit_manifest(root, index, lambda data: data["all_nodeids"].append(new))
        edit_manifest(root, 4, lambda data: data["selected_nodeids"].append(new))
    with pytest.raises(ValueError):
        check_manifests(root, expected_shards=4)


def coverage_reports(tmp_path):
    root = manifests(tmp_path)
    for index in range(1, 5):
        path = root / f"pytest-shard-{index}" / f".coverage.shard-{index}"
        data = CoverageData(basename=str(path))
        data.add_arcs({"bridge/example.py": [(-1, 1), (1, -1)]})
        data.write()
        data.close()
    return root


def run_report_checker(root):
    return subprocess.run(
        [sys.executable, str(ROOT / "tools/check_pytest_shards.py"), str(root), "--shards=4"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_cli_accepts_readable_coverage_from_every_shard(tmp_path):
    completed = run_report_checker(coverage_reports(tmp_path))

    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.parametrize("index", [1, 2, 3, 4])
@pytest.mark.parametrize("fault", ["missing", "corrupt", "empty", "empty_sqlite", "corrupt_metadata"])
def test_cli_rejects_missing_or_unreadable_coverage_from_any_shard(tmp_path, index, fault):
    root = coverage_reports(tmp_path)
    path = root / f"pytest-shard-{index}" / f".coverage.shard-{index}"
    if fault == "missing":
        path.unlink()
    elif fault == "corrupt":
        path.write_bytes(b"not a coverage database")
    elif fault == "empty_sqlite":
        path.unlink()
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("VACUUM")
    elif fault == "corrupt_metadata":
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("UPDATE meta SET value = 'corrupt' WHERE key = 'has_arcs'")
    else:
        path.write_bytes(b"")

    completed = run_report_checker(root)

    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert str(path) in completed.stdout
    assert "Traceback" not in completed.stderr
