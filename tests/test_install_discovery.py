from __future__ import annotations

from pathlib import Path

import pytest

from bridge.install_support import (
    build_minimal_configuration,
    configure_environment,
    discover_sillytavern,
    sillytavern_environment_updates,
)


def _make_sillytavern(
    root: Path,
    *,
    data_root: Path | None = None,
    users: tuple[str, ...] = ("default-user",),
) -> Path:
    root.mkdir(parents=True)
    (root / "server.js").write_text("// fixture\n", encoding="utf-8")
    (root / "package.json").write_text('{"name":"sillytavern","scripts":{"start":"node server.js"}}\n', encoding="utf-8")
    actual_data = data_root or root / "data"
    config_value = str(actual_data) if data_root is not None else "./data"
    (root / "config.yaml").write_text(f"dataRoot: {config_value}\n", encoding="utf-8")
    for name in users:
        user = actual_data / name
        for dirname in ("characters", "worlds", "sysprompt", "User Avatars"):
            (user / dirname).mkdir(parents=True, exist_ok=True)
        (user / "settings.json").write_text("{}\n", encoding="utf-8")
    return actual_data


def test_discover_sillytavern_reads_custom_data_root_and_users(tmp_path: Path):
    home = tmp_path / "home"
    data = tmp_path / "native-data"
    root = home / "SillyTavern"
    _make_sillytavern(root, data_root=data, users=("alice",))

    found = discover_sillytavern(home=home, environ={})

    assert found == [{"root": str(root), "data_root": str(data), "users": ["alice"]}]


def test_discover_sillytavern_prefers_explicit_environment_path(tmp_path: Path):
    home = tmp_path / "home"
    common = home / "SillyTavern"
    explicit = home / "apps" / "SillyTavern"
    _make_sillytavern(common)
    _make_sillytavern(explicit)

    found = discover_sillytavern(home=home, environ={"SILLYTAVERN_DIR": str(explicit)})

    assert found[0]["root"] == str(explicit)
    assert {item["root"] for item in found} == {str(explicit), str(common)}


def test_discover_sillytavern_ignores_false_positive_directory(tmp_path: Path):
    home = tmp_path / "home"
    fake = home / "SillyTavern"
    fake.mkdir(parents=True)
    (fake / "server.js").write_text("// not enough markers\n", encoding="utf-8")

    assert discover_sillytavern(home=home, environ={}) == []


def test_sillytavern_environment_updates_use_detected_user_data_paths(tmp_path: Path):
    root = tmp_path / "SillyTavern"
    data = tmp_path / "data-root"
    _make_sillytavern(root, data_root=data, users=("alice",))
    installation = {"root": str(root), "data_root": str(data), "users": ["alice"]}

    updates = sillytavern_environment_updates(installation, "alice")

    assert updates["SILLYTAVERN_DIR"] == str(root)
    assert updates["SILLYTAVERN_CHARACTER_DIR"] == str(data / "alice" / "characters")
    assert updates["SILLYTAVERN_WORLD_DIR"] == str(data / "alice" / "worlds")
    assert updates["SILLYTAVERN_SYSTEM_PROMPTS_DIR"] == str(data / "alice" / "sysprompt")
    assert updates["SILLYTAVERN_NATIVE_SETTINGS_FILE"] == str(data / "alice" / "settings.json")
    assert updates["SILLYTAVERN_NATIVE_AVATAR_DIR"] == str(data / "alice" / "User Avatars")


def test_build_minimal_configuration_derives_provider_allowed_host():
    updates = build_minimal_configuration(
        bot_token="123456:synthetic",
        allowed_user="12345",
        model="provider::model",
        endpoint="https://api.example.com/v1",
        api_key="secret",
    )

    assert updates == {
        "SILLYTAVERN_TELEGRAM_BOT_TOKEN": "123456:synthetic",
        "SILLYTAVERN_TELEGRAM_ALLOWED_USERS": "12345",
        "SILLYTAVERN_MODEL": "provider::model",
        "SILLYTAVERN_PROVIDER_ENDPOINT": "https://api.example.com/v1",
        "SILLYTAVERN_PROVIDER_ALLOWED_HOSTS": "api.example.com",
        "LLM_API_KEY": "secret",
    }


@pytest.mark.parametrize("allowed_user", ["", "abc", "12,abc"])
def test_build_minimal_configuration_rejects_invalid_allowed_user(allowed_user: str):
    with pytest.raises(ValueError):
        build_minimal_configuration(
            bot_token="123456:synthetic",
            allowed_user=allowed_user,
            model="provider::model",
            endpoint="https://api.example.com/v1",
            api_key="secret",
        )


def test_configure_environment_preserves_unrelated_values_and_replaces_managed_keys(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(
        "# existing config\n"
        "CUSTOM_KEEP=yes\n"
        "SILLYTAVERN_TELEGRAM_BOT_TOKEN=old-token\n"
        "SILLYTAVERN_MODEL=old::model\n",
        encoding="utf-8",
    )
    env.chmod(0o600)

    configure_environment(
        env,
        {
            "SILLYTAVERN_TELEGRAM_BOT_TOKEN": "new-token",
            "SILLYTAVERN_TELEGRAM_ALLOWED_USERS": "12345",
            "SILLYTAVERN_MODEL": "new::model",
        },
    )

    text = env.read_text(encoding="utf-8")
    assert "CUSTOM_KEEP=yes" in text
    assert text.count("SILLYTAVERN_TELEGRAM_BOT_TOKEN=") == 1
    assert "SILLYTAVERN_TELEGRAM_BOT_TOKEN=new-token" in text
    assert "SILLYTAVERN_TELEGRAM_ALLOWED_USERS=12345" in text
    assert "SILLYTAVERN_MODEL=new::model" in text
    assert "old-token" not in text
    assert oct(env.stat().st_mode & 0o777) == "0o600"


def test_configure_environment_rejects_newlines_in_values(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("CUSTOM_KEEP=yes\n", encoding="utf-8")
    env.chmod(0o600)

    with pytest.raises(ValueError):
        configure_environment(env, {"SILLYTAVERN_MODEL": "provider::model\nINJECT=yes"})
