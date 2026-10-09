"""Combined shard reports must prove exact complete test coverage before merging coverage data."""

import json

import pytest


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
