"""Parallel jobs must execute disjoint whole-file partitions of complete discovery."""

from collections import Counter
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("timings", [None, {"tests/test_medium.py": (20.0, 5)}])
def test_file_partitions_cover_every_item_once_without_splitting_files(timings):
    from tools.pytest_shard import partition_items

    items = [
        SimpleNamespace(nodeid=f"tests/test_{file}.py::test_{case}")
        for file, count in (("large", 9), ("medium", 5), ("small", 3), ("tiny", 1), ("extra", 2))
        for case in range(count)
    ]
    shards = [partition_items(items, index, 4, timings=timings) for index in range(1, 5)]
    assert Counter(item.nodeid for shard in shards for item in shard) == Counter(item.nodeid for item in items)
    for file in {item.nodeid.split("::", 1)[0] for item in items}:
        assert sum(any(item.nodeid.startswith(file + "::") for item in shard) for shard in shards) == 1
    assert all(shards)


@pytest.mark.parametrize("timings", [None, {"tests/test_b.py": (20.0, 3), "tests/test_c.py": (20.0, 3)}])
def test_assignment_is_deterministic_and_preserves_collection_order(timings):
    from tools.pytest_shard import partition_items

    items = [SimpleNamespace(nodeid=f"tests/test_{file}.py::test_{case}") for file in "dcba" for case in range(3)]
    for index in range(1, 5):
        selected = partition_items(items, index, 4, timings=timings)
        reverse_selected = partition_items(list(reversed(items)), index, 4, timings=timings)
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


def test_costly_low_count_files_are_distributed_by_measured_hints():
    from tools.pytest_shard import partition_items

    items = [SimpleNamespace(nodeid=f"tests/test_{letter}.py::test_case") for letter in "abcdefghijklmnop"]
    expensive_files = {f"tests/test_{letter}.py" for letter in "aeim"}
    timings = {filename: (60.0, 1) for filename in expensive_files}
    legacy = partition_items(items, 1, 4)
    assert {item.nodeid.split("::", 1)[0] for item in legacy} == expensive_files
    weighted = [partition_items(items, index, 4, timings=timings) for index in range(1, 5)]
    assert all(sum(item.nodeid.split("::", 1)[0] in expensive_files for item in shard) == 1 for shard in weighted)


def test_timing_hints_handle_new_tests_growth_shrinkage_and_missing_files():
    from tools.pytest_shard import estimate_file_cost

    timings = {"tests/test_slow.py": (20.0, 4), "tests/test_removed.py": (100.0, 1)}
    assert estimate_file_cost("tests/test_new.py", 10, timings) == pytest.approx(1.0)
    assert estimate_file_cost("tests/test_slow.py", 4, timings) == 20.0
    assert estimate_file_cost("tests/test_slow.py", 8, timings) == 40.0
    assert estimate_file_cost("tests/test_slow.py", 2, timings) == 20.0
    assert estimate_file_cost("tests/test_new.py", 10, None) == 10
    assert estimate_file_cost("tests/test_fast.py", 100, {"tests/test_fast.py": (1.0, 100)}) == 10.0


def test_nonfinite_scaled_timing_cost_is_rejected():
    from tools.pytest_shard import estimate_file_cost

    with pytest.raises(ValueError, match="timing"):
        estimate_file_cost("tests/test_slow.py", 2, {"tests/test_slow.py": (1e308, 1)})


def test_nonfinite_accumulated_shard_cost_is_rejected():
    from tools.pytest_shard import partition_items

    items = [SimpleNamespace(nodeid=f"tests/test_{letter}.py::test_case") for letter in "ab"]
    timings = {f"tests/test_{letter}.py": (1e308, 1) for letter in "ab"}
    with pytest.raises(ValueError, match="timing"):
        partition_items(items, 1, 1, timings=timings)


def test_profile_uses_median_of_recorded_observed_minimums(tmp_path):
    import json

    from tools.pytest_shard import load_timings

    profile = tmp_path / "timings.json"
    profile.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "files": {"tests/test_slow.py": {"test_count": 4, "observed_minimum_seconds": [30, 10, 40, 20]}},
            }
        )
    )
    assert load_timings(profile) == {"tests/test_slow.py": (25.0, 4)}


@pytest.mark.parametrize(
    "hint",
    [
        {"test_count": 0, "observed_minimum_seconds": [1]},
        {"test_count": -1, "observed_minimum_seconds": [1]},
        {"test_count": True, "observed_minimum_seconds": [1]},
        {"test_count": 1.5, "observed_minimum_seconds": [1]},
        {"test_count": 1, "observed_minimum_seconds": []},
        {"test_count": 1, "observed_minimum_seconds": [0]},
        {"test_count": 1, "observed_minimum_seconds": [-1]},
        {"test_count": 1, "observed_minimum_seconds": [True]},
        {"test_count": 1, "observed_minimum_seconds": ["1"]},
        {"test_count": 1, "observed_minimum_seconds": [float("inf")]},
        {"test_count": 1, "observed_minimum_seconds": [float("nan")]},
        {"test_count": 1, "observed_minimum_seconds": [1e308, 1e308]},
        {"test_count": 1, "observed_minimum_seconds": [10**400]},
        {"test_count": 1},
        [],
    ],
)
def test_invalid_timing_hints_are_rejected(tmp_path, hint):
    import json

    from tools.pytest_shard import load_timings

    profile = tmp_path / "timings.json"
    profile.write_text(json.dumps({"schema_version": 1, "files": {"tests/test_slow.py": hint}}))
    with pytest.raises(ValueError, match="timing"):
        load_timings(profile)


@pytest.mark.parametrize(
    "data",
    [
        [],
        {},
        {"schema_version": 2, "files": {}},
        {"schema_version": True, "files": {}},
        {"schema_version": 1, "files": []},
    ],
)
def test_invalid_timing_profile_schema_is_rejected(tmp_path, data):
    import json

    from tools.pytest_shard import load_timings

    profile = tmp_path / "timings.json"
    profile.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="timing"):
        load_timings(profile)


@pytest.mark.parametrize("samples, reference_count", [([1e308, 1e308], 2), ([10**400], 2), ([1e308], 1)])
def test_numeric_profile_errors_exit_pytest_as_usage_errors(tmp_path, samples, reference_count):
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).parents[1]) + os.pathsep + environment.get("PYTHONPATH", "")
    (tmp_path / "test_sample.py").write_text("def test_one(): pass\ndef test_two(): pass\n")
    (tmp_path / "timings.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "files": {"test_sample.py": {"test_count": reference_count, "observed_minimum_seconds": samples}},
            }
        )
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "tools.pytest_shard",
            "--shard-index=1",
            "--shard-count=1",
            "--shard-timings=timings.json",
            "--shard-manifest=manifest.json",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == pytest.ExitCode.USAGE_ERROR, result.stdout + result.stderr
    assert "timing" in result.stderr
    assert "INTERNALERROR" not in result.stdout + result.stderr
    assert not (tmp_path / "manifest.json").exists()


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
    timings = tmp_path / "timings.json"
    timings.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "files": {"test_a.py": {"test_count": 2, "observed_minimum_seconds": [5, 6, 7, 8]}},
            }
        )
    )
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
            f"--shard-timings={timings}",
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
