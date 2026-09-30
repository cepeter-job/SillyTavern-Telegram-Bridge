import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def inputs(tmp_path):
    home = tmp_path / "home"
    source = home / "bridge"
    source.mkdir(parents=True)
    (source / ".env.example").write_text((ROOT / ".env.example").read_text())
    (source / "sillytavern_telegram_bridge.py").write_text("# fixture")
    env = home / "state/.env"
    units = home / ".config/systemd/user"
    return home, source, env, units


def test_fresh_install_has_private_config_starters_service_and_idempotency(tmp_path):
    from bridge.card_content import parse_png_chara_bytes
    from bridge.install_support import prepare_install

    home, source, env, units = inputs(tmp_path)
    result = prepare_install(source, home, env, units)
    assert env.is_file() and env.stat().st_mode & 0o077 == 0
    original = env.read_bytes()
    assert result["unit_managed"] is True
    unit = units / "sillytavern-telegram.service"
    assert "--user" not in unit.read_text()
    assert "NoNewPrivileges=true" in unit.read_text()
    assert "SILLYTAVERN_ENV_FILE" in unit.read_text()
    cards = list((home / ".local/share/SillyTavern/data/default-user/characters").glob("*.png"))
    assert cards and parse_png_chara_bytes(cards[0].read_bytes())
    prepare_install(source, home, env, units)
    assert env.read_bytes() == original
    assert len(cards) == 1


def test_fresh_install_sets_standard_update_signer_path(tmp_path):
    from bridge.install_support import prepare_install

    home, source, env, units = inputs(tmp_path)
    prepare_install(source, home, env, units)
    active = [line for line in env.read_text().splitlines() if line.startswith("SILLYTAVERN_UPDATE_ALLOWED_SIGNERS=")]
    assert active == [f"SILLYTAVERN_UPDATE_ALLOWED_SIGNERS={home / '.config/sillytavern-telegram/trusted-maintainers'}"]


def test_fresh_install_preserves_active_template_update_signer_path(tmp_path):
    from bridge.install_support import prepare_install

    home, source, env, units = inputs(tmp_path)
    template = (source / ".env.example").read_text()
    template = template.replace(
        "# SILLYTAVERN_UPDATE_ALLOWED_SIGNERS=/path/to/private-config/trusted-maintainers",
        "SILLYTAVERN_UPDATE_ALLOWED_SIGNERS=/custom/trusted-maintainers",
    )
    (source / ".env.example").write_text(template)
    prepare_install(source, home, env, units)
    active = [line for line in env.read_text().splitlines() if line.startswith("SILLYTAVERN_UPDATE_ALLOWED_SIGNERS=")]
    assert active == ["SILLYTAVERN_UPDATE_ALLOWED_SIGNERS=/custom/trusted-maintainers"]


def test_existing_private_env_native_files_and_custom_service_are_preserved(tmp_path):
    from bridge.install_support import prepare_install

    home, source, env, units = inputs(tmp_path)
    env.parent.mkdir(parents=True)
    env.write_text("LLM_API_KEY='$(touch /tmp/not-executed)'\n")
    env.chmod(0o600)
    units.mkdir(parents=True)
    unit = units / "sillytavern-telegram.service"
    unit.write_text("# User managed service\n")
    before = env.read_bytes()
    result = prepare_install(source, home, env, units)
    assert env.read_bytes() == before
    assert unit.read_text() == "# User managed service\n"
    assert result["unit_managed"] is False


def test_provider_catalog_can_be_prepared_using_only_env_values(tmp_path):
    from bridge.install_support import installation_settings, prepare_install, validate_install

    home, source, env, units = inputs(tmp_path)
    prepare_install(source, home, env, units)
    env.write_text(
        "SILLYTAVERN_TELEGRAM_BOT_TOKEN=123456:synthetic\nSILLYTAVERN_TELEGRAM_ALLOWED_USERS=12345\nSILLYTAVERN_MODEL=test::sample\nLLM_API_KEY=private-key\nSILLYTAVERN_PROVIDER_ENDPOINT=https://provider.example/v1\nSILLYTAVERN_PROVIDER_ALLOWED_HOSTS=provider.example\nSILLYTAVERN_MINIAPP_PUBLIC_URL=https://bridge.example/miniapp/\n"
    )
    prepare_install(source, home, env, units)
    settings = installation_settings(source, home, env)
    assert settings.provider_config_file.is_file()
    assert "private-key" not in settings.provider_config_file.read_text()
    assert "private-key" not in (units / "sillytavern-telegram.service").read_text()
    assert validate_install(source, home, env) == []
    assert not (env.parent / "Caddyfile.miniapp").exists()
    assert "proxy_path" not in prepare_install(source, home, env, units)


def test_installer_rejects_symlink_env_and_never_evaluates_values(tmp_path):
    from bridge.install_support import prepare_install

    home, source, env, units = inputs(tmp_path)
    target = home / "secret"
    target.write_text("keep")
    target.chmod(0o600)
    env.parent.mkdir(parents=True)
    env.symlink_to(target)
    with pytest.raises(ValueError):
        prepare_install(source, home, env, units)
    assert target.read_text() == "keep"


def test_install_script_help_and_bash_syntax_are_safe():
    script = ROOT / "install.sh"
    assert script.exists()
    subprocess.run(["/bin/bash", "-n", str(script)], check=True)
    result = subprocess.run(["/bin/bash", str(script), "--help"], capture_output=True, text=True, check=True)
    assert "--no-start" in result.stdout and "--with-tailscale-funnel" in result.stdout
    assert "--release" in result.stdout and "--allowed-signers" in result.stdout and "--unsafe-main" in result.stdout
    assert "--with-caddy" not in result.stdout


def test_missing_credential_diagnostic_never_echoes_provider_controlled_name(tmp_path, monkeypatch):
    import bridge.provider_catalog as catalog
    from bridge.install_support import prepare_install, validate_install

    home, source, env, units = inputs(tmp_path)
    prepare_install(source, home, env, units)
    env.write_text(
        "SILLYTAVERN_TELEGRAM_BOT_TOKEN=123456:synthetic\nSILLYTAVERN_TELEGRAM_ALLOWED_USERS=12345\nSILLYTAVERN_MODEL=test::sample\nSILLYTAVERN_PROVIDER_ALLOWED_HOSTS=provider.example\n"
    )
    marker = "provider-controlled-sensitive-name"
    monkeypatch.setattr(
        catalog,
        "load_routing_catalog",
        lambda **kwargs: {
            "test": {"models": ["sample"], "api_endpoint": "https://provider.example/v1", "api_key_env": marker}
        },
    )
    errors = validate_install(source, home, env)
    assert errors
    assert marker not in str(errors)


def test_retired_proxy_command_is_rejected():
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "bridge.install_support", "public-url", "--source", ".", "--env", "absent"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "invalid choice" in result.stderr


def _bootstrap_installer_fixture(tmp_path):
    import os

    bootstrap = tmp_path / "bootstrap"
    bootstrap.mkdir()
    script = bootstrap / "install.sh"
    script.write_bytes((ROOT / "install.sh").read_bytes())
    script.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    git_log = tmp_path / "git.log"

    def executable(name: str, body: str):
        path = fakebin / name
        path.write_text("#!/bin/sh\nset -eu\n" + body, encoding="utf-8")
        path.chmod(0o755)

    executable("uname", "echo Linux\n")
    executable(
        "systemctl",
        'if [ "${1:-}" = --user ] && [ "${2:-}" = is-active ]; then exit 3; fi\nexit 0\n',
    )
    executable("loginctl", 'if [ "${1:-}" = show-user ]; then echo yes; fi\nexit 0\n')
    executable(
        "ssh-keygen",
        'if [ "${1:-}" = -lf ]; then '
        'cat >/dev/null; '
        'echo "256 SHA256:nCiZP+h1YWYCFjh37W8tXjR7oWGpZPF6bP4lbTOlAiI cepeter-release-signing (ED25519)"; '
        "fi\nexit 0\n",
    )
    executable(
        "git",
        f"""printf '%s\\n' "$*" >> {git_log}\n
if [ "${{1:-}}" = clone ]; then
  eval "target=\\${{${{#}}}}"
  mkdir -p "$target/bridge" "$target/.venv/bin"
  : > "$target/bridge/main.py"
  : > "$target/bridge/install_support.py"
  : > "$target/requirements.lock"
  cat > "$target/.venv/bin/python" <<'PYWRAP'
#!/bin/sh
if [ "${1:-}" = -c ]; then printf '%s\\n' "${3:-}"; fi
exit 0
PYWRAP
  chmod +x "$target/.venv/bin/python"
  exit 0
fi
case "$*" in
  *"rev-parse"*) printf '%040d\\n' 0;;
esac
exit 0
""",
    )
    env = dict(os.environ)
    env.update(HOME=str(home), PATH=str(fakebin) + ":/usr/bin:/bin", GIT_LOG=str(git_log))
    return script, home, git_log, env


def test_clone_install_requires_explicit_release_or_unsafe_main(tmp_path):
    script, _home, git_log, env = _bootstrap_installer_fixture(tmp_path)
    result = subprocess.run(
        ["/bin/bash", str(script), "--no-deps", "--no-start"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "--release" in result.stderr and "--unsafe-main" in result.stderr
    assert not git_log.exists()


def test_release_clone_verifies_signed_tag_before_checkout(tmp_path):
    script, home, git_log, env = _bootstrap_installer_fixture(tmp_path)
    signers = home / "trusted-maintainers"
    signers.write_text("maintainer ssh-ed25519 synthetic-public-key\n", encoding="utf-8")
    signers.chmod(0o600)
    result = subprocess.run(
        [
            "/bin/bash",
            str(script),
            "--release",
            "v0.2.039",
            "--allowed-signers",
            str(signers),
            "--no-deps",
            "--no-start",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    calls = git_log.read_text(encoding="utf-8")
    assert "clone --no-checkout" in calls
    assert "gpg.format=ssh" in calls and "gpg.ssh.allowedSignersFile=" in calls
    assert "verify-tag v0.2.039" in calls
    assert "rev-parse v0.2.039^{commit}" in calls
    assert "checkout -B main" in calls
    assert "clone --branch main" not in calls


def test_unsafe_main_clone_is_explicit_and_does_not_claim_signature_verification(tmp_path):
    script, _home, git_log, env = _bootstrap_installer_fixture(tmp_path)
    result = subprocess.run(
        ["/bin/bash", str(script), "--unsafe-main", "--no-deps", "--no-start"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    calls = git_log.read_text(encoding="utf-8")
    assert "clone --branch main --single-branch" in calls
    assert "verify-tag" not in calls


def test_release_clone_bootstraps_standard_pinned_trust_file(tmp_path):
    script, home, git_log, env = _bootstrap_installer_fixture(tmp_path)
    result = subprocess.run(
        [
            "/bin/bash",
            str(script),
            "--release",
            "v0.2.039",
            "--no-deps",
            "--no-start",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    signers = home / ".config/sillytavern-telegram/trusted-maintainers"
    assert signers.is_file()
    assert signers.stat().st_mode & 0o077 == 0
    assert "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGRaxgobK+D+zdXdUzLb1xTQ2EPs9iYkeQGOOlepl+35" in signers.read_text()
    assert "verify-tag v0.2.039" in git_log.read_text(encoding="utf-8")


def test_install_script_contains_interactive_standard_choices_and_multi_distro_detection():
    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    for expected in (
        "Standard install",
        "Install + Tailscale Mini App",
        "Prepare only",
        "/etc/os-release",
        "apt-get",
        "dnf",
        "yum",
        "pacman",
        "zypper",
    ):
        assert expected in text
