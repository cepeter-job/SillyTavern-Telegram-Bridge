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
    config = (env.parent / "Caddyfile.miniapp").read_text()
    assert "127.0.0.1:8787" in config and "bridge.example" in config


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
    assert "--no-start" in result.stdout and "--with-caddy" in result.stdout
