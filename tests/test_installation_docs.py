import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _section(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def test_readme_quick_start_uses_guided_installer_instead_of_manual_bootstrap():
    quick = _section((ROOT / "README.md").read_text(), "## Quick start", "## What you can do")
    blocks = re.findall(r"```bash\n(.*?)\n```", quick, flags=re.DOTALL)
    assert blocks, "Quick start must contain one copy/paste Bash install block"
    script = blocks[0]
    assert "raw.githubusercontent.com/cepeter/SillyTavern-Telegram-Bridge/main/install.sh" in script
    assert "sillytavern-telegram-install.sh" in script
    assert "Standard install" in quick
    assert "Install + Tailscale Mini App" in quick
    assert "git clone --no-checkout" not in quick
    assert "verify-tag" not in quick
    assert "--unsafe-main" not in quick


def test_readme_basic_flow_uses_providers_and_starts_before_chatting():
    text = (ROOT / "README.md").read_text()
    basic = _section(text, "## Basic bot use", "## Documentation")
    assert "/model" not in basic
    assert "/providers" in basic
    assert basic.index("/start") < basic.index("Send normal messages")


def test_readme_shows_standard_install_before_optional_funnel_choice():
    quick = _section((ROOT / "README.md").read_text(), "## Quick start", "## What you can do")
    assert "Standard install" in quick
    assert "Install + Tailscale Mini App" in quick
    assert quick.index("Standard install") < quick.index("Install + Tailscale Mini App")


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


def test_installation_primary_flow_is_guided_and_preserves_existing_state():
    text = (ROOT / "docs/installation.md").read_text()
    primary = text.split("## Advanced / development installation", 1)[0]
    assert "cp .env.example" not in primary
    assert "systemd/sillytavern-telegram.service.example" not in primary
    assert "Standard install" in primary
    assert "Install + Tailscale Mini App" in primary
    assert "Configure later" in primary
    assert "existing" in primary.lower() and ".env" in primary


def test_installation_guide_has_no_second_competing_installation_flow():
    text = (ROOT / "docs/installation.md").read_text()
    assert "## 📦 Installation guide" not in text
    assert "Version **0.2.033**" not in text


def test_installation_primary_flow_documents_autodetection():
    text = (ROOT / "docs/installation.md").read_text()
    primary = text.split("## Advanced / development installation", 1)[0]
    for expected in ("apt-get", "dnf", "pacman", "zypper", "dataRoot", "SILLYTAVERN_DIR"):
        assert expected in primary


def test_manual_git_update_verifies_signed_tag_before_checkout():
    text = (ROOT / "docs/operations.md").read_text()
    manual = _section(text, "### Manual update", "### Database migrations")
    verify = 'verify-tag "$tag"'
    checkout = 'git checkout -B main "$tag^{commit}"'
    assert verify in manual
    assert checkout in manual
    assert manual.index(verify) < manual.index(checkout)
    assert "git pull --ff-only origin main" not in manual
