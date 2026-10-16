"""Valid Python call forms must not crash the resource-leak gate."""

from __future__ import annotations

import pytest

from tools.leakscan import Scanner


@pytest.mark.parametrize("name", ["append", "concat", "concatenate", "vstack", "hstack"])
@pytest.mark.parametrize("qualified", [False, True])
def test_loop_rebuild_calls_accept_bare_and_qualified_names(tmp_path, name, qualified):
    call = f"array.{name}" if qualified else name
    source = tmp_path / "loop.py"
    source.write_text(f"for i in range(3):\n    {call}(i)\n", encoding="utf-8")
    scanner = Scanner()

    scanner.scan_path(str(source))

    findings = [finding for finding in scanner.findings if finding.rule == "DATAFRAME_REBUILD_IN_LOOP"]
    # A bare append has no receiver establishing dataframe/array rebuilding.
    if name == "append" and not qualified:
        assert findings == []
    else:
        assert len(findings) == 1
        assert findings[0].line == 2
        assert f"`{call}()`" in findings[0].message


def test_ordinary_list_append_does_not_report_dataframe_rebuilding(tmp_path):
    source = tmp_path / "loop.py"
    source.write_text("items = []\nfor i in range(3):\n    items.append(i)\n", encoding="utf-8")
    scanner = Scanner()

    scanner.scan_path(str(source))

    assert all(finding.rule != "DATAFRAME_REBUILD_IN_LOOP" for finding in scanner.findings)
