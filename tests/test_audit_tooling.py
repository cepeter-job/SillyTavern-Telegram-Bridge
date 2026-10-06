"""Architecture audits must recognize real entrypoints and ratchet existing debt."""

from __future__ import annotations

import importlib
import importlib.util

import pytest


def tool(name):
    assert importlib.util.find_spec(name) is not None, f"Missing audit tool: {name}"
    return importlib.import_module(name)


def test_size_ratchet_accepts_cohesive_small_modules_and_existing_debt():
    check = tool("tools.check_module_sizes").size_errors
    assert check({"bridge/small.py": 8, "bridge/large.py": 700}, {"bridge/large.py": 700}) == []


@pytest.mark.parametrize(
    "sizes, baseline",
    [
        ({"bridge/new.py": 501}, {}),
        ({"bridge/large.py": 701}, {"bridge/large.py": 700}),
        ({"bridge/large.py": 650}, {"bridge/large.py": 700}),
        ({"bridge/large.py": 490}, {"bridge/large.py": 700}),
        ({}, {"bridge/removed.py": 700}),
    ],
)
def test_size_ratchet_rejects_growth_and_stale_exceptions(sizes, baseline):
    assert tool("tools.check_module_sizes").size_errors(sizes, baseline)


def test_size_ratchet_rejects_baseline_increase_against_reviewed_base():
    check = tool("tools.check_module_sizes").size_errors
    assert check({"bridge/large.py": 710}, {"bridge/large.py": 710}, previous={"bridge/large.py": 700})
    assert check({"bridge/new.py": 600}, {"bridge/new.py": 600}, previous={})


def test_reference_audit_recognizes_aliases_local_imports_cli_and_path_workers(tmp_path):
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    sources = {
        "__init__.py": "",
        "main.py": "def initialize():\n    from bridge import director_runtime\n",
        "store.py": "from bridge.memory_snapshot_repository import snapshot_local_memory as _rows\n",
        "document_extraction.py": "from pathlib import Path\np = Path(__file__).with_name('pdf_parser.py')\n",
        "director_runtime.py": "",
        "memory_snapshot_repository.py": "",
        "tailscale_funnel.py": "",
        "pdf_parser.py": "",
        "unused.py": "",
    }
    for name, value in sources.items():
        (bridge / name).write_text(value)
    (tmp_path / "install.sh").write_text('"$PY" -m bridge.tailscale_funnel prepare\n')
    refs = tool("tools.audit_module_references").collect_references(tmp_path)
    assert any("main.py" in item for item in refs["bridge.director_runtime"])
    assert any("store.py" in item for item in refs["bridge.memory_snapshot_repository"])
    assert any("install.sh" in item for item in refs["bridge.tailscale_funnel"])
    assert any("document_extraction.py" in item for item in refs["bridge.pdf_parser"])
    assert refs["bridge.unused"] == []
