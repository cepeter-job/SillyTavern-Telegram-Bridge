import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _section(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def test_readme_quick_start_discovers_latest_signed_release_before_main_checkout():
    quick = _section((ROOT / "README.md").read_text(), "## Quick start", "## What you can do")
    assert "One-time trust setup" in quick
    assert "Install latest signed release" in quick
    install_part = quick.split("Install latest signed release", 1)[1]
    blocks = re.findall(r"```bash\n(.*?)\n```", install_part, flags=re.DOTALL)
    assert blocks, "Quick start must contain one copy/paste Bash install block"
    script = blocks[0]

    discovery = "git tag --list 'v*' --sort=-version:refname"
    verify = 'verify-tag "$tag"'
    checkout = 'git checkout -B main "$tag^{commit}"'
    install = "./install.sh --system-deps --no-start"
    for expected in ("git clone --no-checkout", discovery, verify, checkout, install):
        assert expected in script
    assert script.index(discovery) < script.index(verify) < script.index(checkout) < script.index(install)
    assert "rev-parse" not in script
    assert "vX.Y.Z" not in quick
    assert "--unsafe-main" not in quick


def test_installation_guide_keeps_bootstrap_and_unsafe_paths_in_advanced_section():
    text = (ROOT / "docs/installation.md").read_text()
    leading = text.split("## Advanced / development", 1)[0]
    assert "--release vX.Y.Z" not in leading
    assert "--unsafe-main" not in leading
    advanced = text.split("## Advanced / development", 1)[1]
    assert "--release" in advanced
    assert "--unsafe-main" in advanced
    assert "ZIP" in advanced
