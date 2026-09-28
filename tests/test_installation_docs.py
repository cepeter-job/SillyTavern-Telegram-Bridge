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


def test_readme_basic_flow_uses_providers_and_starts_before_chatting():
    text = (ROOT / "README.md").read_text()
    basic = _section(text, "## Basic bot use", "## Documentation")
    assert "/model" not in basic
    assert "/providers" in basic
    assert basic.index("/start") < basic.index("Send normal messages")


def test_readme_shows_telegram_only_start_before_optional_funnel():
    quick = _section((ROOT / "README.md").read_text(), "## Quick start", "## What you can do")
    assert "./install.sh --linger" in quick
    assert "./install.sh --with-tailscale-funnel --linger" in quick
    assert quick.index("./install.sh --linger") < quick.index("./install.sh --with-tailscale-funnel --linger")


def test_miniapp_upgrade_guidance_never_pulls_unsigned_main():
    text = (ROOT / "docs/miniapp.md").read_text()
    assert "git pull --ff-only origin main" not in text
    assert "Version 0.2.033" not in text
    assert "operations.md#manual-update" in text


def test_installation_guide_keeps_bootstrap_and_unsafe_paths_in_advanced_section():
    text = (ROOT / "docs/installation.md").read_text()
    leading = text.split("## Advanced / development", 1)[0]
    assert "--release vX.Y.Z" not in leading
    assert "--unsafe-main" not in leading
    advanced = text.split("## Advanced / development", 1)[1]
    assert "--release" in advanced
    assert "--unsafe-main" in advanced
    assert "ZIP" in advanced


def test_installation_primary_flow_does_not_overwrite_installer_managed_state():
    text = (ROOT / "docs/installation.md").read_text()
    primary = text.split("## Advanced / development installation", 1)[0]
    assert "cp .env.example" not in primary
    assert "systemd/sillytavern-telegram.service.example" not in primary
    assert "./install.sh --linger" in primary
    assert "./install.sh --with-tailscale-funnel --linger" in primary


def test_installation_guide_has_no_second_competing_installation_flow():
    text = (ROOT / "docs/installation.md").read_text()
    assert "## 📦 Installation guide" not in text
    assert "Version **0.2.033**" not in text


def test_installation_bootstrap_lists_preverification_tools():
    text = (ROOT / "docs/installation.md").read_text()
    prerequisites = _section(text, "## Before you start", "## Install with the user-scope script")
    assert "git" in prerequisites
    assert "ssh-keygen" in prerequisites
    assert "openssh-client" in prerequisites


def test_manual_git_update_verifies_signed_tag_before_checkout():
    text = (ROOT / "docs/operations.md").read_text()
    manual = _section(text, "### Manual update", "### Database migrations")
    verify = 'verify-tag "$tag"'
    checkout = 'git checkout -B main "$tag^{commit}"'
    assert verify in manual
    assert checkout in manual
    assert manual.index(verify) < manual.index(checkout)
    assert "git pull --ff-only origin main" not in manual
