from __future__ import annotations

from pathlib import Path

from bridge.install_wizard import configure_interactively


def _env(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    return path


def test_wizard_collects_minimum_bot_and_openai_compatible_provider_values(tmp_path: Path):
    env = _env(
        tmp_path / ".env",
        "SILLYTAVERN_TELEGRAM_BOT_TOKEN=\n"
        "SILLYTAVERN_TELEGRAM_ALLOWED_USERS=\n"
        "SILLYTAVERN_MODEL=default::replace-model\n"
        "LLM_API_KEY=\n",
    )
    answers = iter(["12345", "1", "provider::model", "https://api.example.com/v1"])
    secrets = iter(["123456:synthetic", "secret-key"])

    complete = configure_interactively(
        env_path=env,
        home=tmp_path,
        input_fn=lambda _prompt: next(answers),
        secret_fn=lambda _prompt: next(secrets),
        output_fn=lambda _line: None,
    )

    text = env.read_text(encoding="utf-8")
    assert complete is True
    assert "SILLYTAVERN_TELEGRAM_BOT_TOKEN=123456:synthetic" in text
    assert "SILLYTAVERN_TELEGRAM_ALLOWED_USERS=12345" in text
    assert "SILLYTAVERN_MODEL=provider::model" in text
    assert "SILLYTAVERN_PROVIDER_ENDPOINT=https://api.example.com/v1" in text
    assert "SILLYTAVERN_PROVIDER_ALLOWED_HOSTS=api.example.com" in text
    assert "LLM_API_KEY=secret-key" in text


def test_wizard_can_leave_provider_for_later_and_reports_incomplete(tmp_path: Path):
    env = _env(
        tmp_path / ".env",
        "SILLYTAVERN_TELEGRAM_BOT_TOKEN=\n"
        "SILLYTAVERN_TELEGRAM_ALLOWED_USERS=\n"
        "SILLYTAVERN_MODEL=default::replace-model\n",
    )
    answers = iter(["12345", "2"])
    secrets = iter(["123456:synthetic"])

    complete = configure_interactively(
        env_path=env,
        home=tmp_path,
        input_fn=lambda _prompt: next(answers),
        secret_fn=lambda _prompt: next(secrets),
        output_fn=lambda _line: None,
    )

    assert complete is False
    text = env.read_text(encoding="utf-8")
    assert "SILLYTAVERN_TELEGRAM_BOT_TOKEN=123456:synthetic" in text
    assert "SILLYTAVERN_TELEGRAM_ALLOWED_USERS=12345" in text
    assert "SILLYTAVERN_MODEL=default::replace-model" in text


def test_wizard_preserves_complete_existing_configuration_without_secret_prompt(tmp_path: Path):
    env = _env(
        tmp_path / ".env",
        "SILLYTAVERN_TELEGRAM_BOT_TOKEN=123456:existing\n"
        "SILLYTAVERN_TELEGRAM_ALLOWED_USERS=12345\n"
        "SILLYTAVERN_MODEL=provider::model\n"
        "SILLYTAVERN_PROVIDER_ENDPOINT=https://api.example.com/v1\n"
        "SILLYTAVERN_PROVIDER_ALLOWED_HOSTS=api.example.com\n"
        "LLM_API_KEY=existing-secret\n",
    )

    complete = configure_interactively(
        env_path=env,
        home=tmp_path,
        input_fn=lambda _prompt: (_ for _ in ()).throw(AssertionError("unexpected input prompt")),
        secret_fn=lambda _prompt: (_ for _ in ()).throw(AssertionError("unexpected secret prompt")),
        output_fn=lambda _line: None,
    )

    assert complete is True
    assert "LLM_API_KEY=existing-secret" in env.read_text(encoding="utf-8")


def test_wizard_auto_selects_single_sillytavern_installation_and_user(tmp_path: Path):
    root = tmp_path / "SillyTavern"
    user = root / "data" / "alice"
    for dirname in ("characters", "worlds", "sysprompt", "User Avatars"):
        (user / dirname).mkdir(parents=True, exist_ok=True)
    (user / "characters" / "Alice.png").write_bytes(b"fixture")
    (user / "settings.json").write_text("{}\n", encoding="utf-8")
    (root / "server.js").write_text("// fixture\n", encoding="utf-8")
    (root / "package.json").write_text('{"name":"sillytavern"}\n', encoding="utf-8")
    (root / "config.yaml").write_text("dataRoot: ./data\n", encoding="utf-8")
    env = _env(
        tmp_path / ".env",
        "SILLYTAVERN_TELEGRAM_BOT_TOKEN=123456:existing\n"
        "SILLYTAVERN_TELEGRAM_ALLOWED_USERS=12345\n"
        "SILLYTAVERN_MODEL=provider::model\n"
        "SILLYTAVERN_PROVIDER_ENDPOINT=https://api.example.com/v1\n"
        "SILLYTAVERN_PROVIDER_ALLOWED_HOSTS=api.example.com\n"
        "LLM_API_KEY=existing-secret\n",
    )

    complete = configure_interactively(
        env_path=env,
        home=tmp_path,
        input_fn=lambda _prompt: (_ for _ in ()).throw(AssertionError("unexpected selection prompt")),
        secret_fn=lambda _prompt: (_ for _ in ()).throw(AssertionError("unexpected secret prompt")),
        output_fn=lambda _line: None,
    )

    text = env.read_text(encoding="utf-8")
    assert complete is True
    assert f"SILLYTAVERN_DIR={root}" in text
    assert f"SILLYTAVERN_CHARACTER_DIR={user / 'characters'}" in text
    assert "SILLYTAVERN_DEFAULT_CHARACTER=Alice.png" in text
