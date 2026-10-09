"""Published release history remains recoverable after latest-only housekeeping."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from bridge.update import _changelog_has_unreleased, _changelog_version

ROOT = Path(__file__).resolve().parents[1]


def test_legacy_changelog_preserves_the_exact_pre_backfill_git_blob():
    raw = (ROOT / "CHANGELOG-legacy.md").read_bytes()
    framed = f"blob {len(raw)}\0".encode("ascii") + raw
    assert hashlib.sha1(framed, usedforsecurity=False).hexdigest() == "6d56385df02fd07087b90ed34b483b1adb1f570b"


def test_backfill_covers_each_published_release_once_in_descending_order():
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    versions = re.findall(r"^## \[(0\.3\.\d{3})\]", text, re.MULTILINE)
    expected = [f"0.3.{number:03d}" for number in range(19, 6, -1)]
    assert [version for version in versions if version in expected] == expected
    assert all(versions.count(version) == 1 for version in expected)
    assert "(CHANGELOG-legacy.md)" in text
    for version in expected:
        section = text.split(f"## [{version}]", 1)[1].split("\n## ", 1)[0]
        assert re.search(r"\*\*Release commit:\*\* `[0-9a-f]{40}`", section)


def test_backfilled_version_is_readable_without_reclassifying_old_work_as_pending(tmp_path):
    source = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    # Freeze a release excerpt so legitimate future entries do not weaken this test.
    excerpt = "# Changelog\n\n## [Unreleased]\n\n## [0.3.019]" + source.split("## [0.3.019]", 1)[1]
    assert "## [0.3.018]" in excerpt
    path = tmp_path / "CHANGELOG.md"
    path.write_text(excerpt, encoding="utf-8")
    assert _changelog_version(path) == "0.3.019"
    assert not _changelog_has_unreleased(path)


def test_native_only_decision_retains_readonly_preview_and_human_review_gates():
    text = (ROOT / "docs/preset-compatibility-decision.md").read_text(encoding="utf-8")
    assert "Do not implement a full SillyTavern Chat Completion preset interpreter" in text
    assert "Pure/read-only" in text
    assert "must never silently rename or reinterpret" in text
    assert "independent blinded human review" in text
