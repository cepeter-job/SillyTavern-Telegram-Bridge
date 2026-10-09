"""Parallel jobs must execute disjoint whole-file partitions of complete discovery."""

from collections import Counter
from types import SimpleNamespace

import pytest


def test_file_partitions_cover_every_item_once_without_splitting_files():
    from tools.pytest_shard import partition_items

    items = [
        SimpleNamespace(nodeid=f"tests/test_{file}.py::test_{case}")
        for file, count in (("large", 9), ("medium", 5), ("small", 3), ("tiny", 1), ("extra", 2))
        for case in range(count)
    ]
    shards = [partition_items(items, index, 4) for index in range(1, 5)]
    assert Counter(item.nodeid for shard in shards for item in shard) == Counter(item.nodeid for item in items)
    for file in {item.nodeid.split("::", 1)[0] for item in items}:
        assert sum(any(item.nodeid.startswith(file + "::") for item in shard) for shard in shards) == 1
    assert all(shards)


def test_assignment_is_deterministic_and_preserves_collection_order():
    from tools.pytest_shard import partition_items

    items = [SimpleNamespace(nodeid=f"tests/test_{file}.py::test_{case}") for file in "dcba" for case in range(3)]
    for index in range(1, 5):
        selected = partition_items(items, index, 4)
        reverse_selected = partition_items(list(reversed(items)), index, 4)
        assert {item.nodeid for item in selected} == {item.nodeid for item in reverse_selected}
        assert selected == [item for item in items if item in selected]


@pytest.mark.parametrize("index, count", [(0, 4), (5, 4), (1, 0), (1, -1)])
def test_invalid_shard_selection_is_rejected(index, count):
    from tools.pytest_shard import partition_items

    with pytest.raises(ValueError, match="shard"):
        partition_items([], index, count)


def test_duplicate_collection_is_rejected():
    from tools.pytest_shard import partition_items

    item = SimpleNamespace(nodeid="tests/test_example.py::test_case")
    with pytest.raises(ValueError, match="duplicate"):
        partition_items([item, item], 1, 4)


@pytest.mark.parametrize("workers", [0, 2])
def test_real_pytest_shards_publish_and_combine_complete_evidence(tmp_path, workers):
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    from tools.check_pytest_shards import check_manifests

    root = Path(__file__).parents[1]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(root) + os.pathsep + environment.get("PYTHONPATH", "")
    application = []
    for letter in "abcd":
        application.append(f"def value_{letter}(flag):\n    if flag:\n        return 1\n    return 0\n")
        (tmp_path / f"test_{letter}.py").write_text(
            f"import pytest\nfrom sample_application import value_{letter}\n"
            f"@pytest.mark.parametrize('flag', [True, False])\n"
            f"def test_value(flag):\n    assert value_{letter}(flag) == int(flag)\n"
        )
    (tmp_path / "sample_application.py").write_text("\n".join(application))
    (tmp_path / ".coveragerc").write_text("[run]\nbranch = true\n[report]\nfail_under = 100\n")
    reports = tmp_path / "reports"
    for index in range(1, 5):
        destination = reports / f"pytest-shard-{index}"
        destination.mkdir(parents=True)
        environment["COVERAGE_FILE"] = str(destination / f".coverage.shard-{index}")
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "tools.pytest_shard",
            f"--shard-index={index}",
            "--shard-count=4",
            f"--shard-manifest={destination / 'shard-manifest.json'}",
            "--cov=sample_application",
            "--cov-report=",
            "--cov-fail-under=0",
        ]
        if workers:
            command.extend(["-n", str(workers), "--dist=loadfile"])
        result = subprocess.run(command, cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=40)
        assert result.returncode == 0, result.stdout + result.stderr
        assert (destination / f".coverage.shard-{index}").is_file()
    assert check_manifests(reports, expected_shards=4) == 8
    environment.pop("COVERAGE_FILE")
    for arguments in (
        ["combine", "--keep", *(str(path) for path in sorted(reports.iterdir()))],
        ["json", "-o", "combined.json"],
    ):
        result = subprocess.run(
            [sys.executable, "-m", "coverage", *arguments],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((tmp_path / "combined.json").read_text())["totals"]["percent_covered"] == 100
