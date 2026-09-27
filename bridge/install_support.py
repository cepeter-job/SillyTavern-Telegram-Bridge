"""User-scope installation preparation; private configuration is data, never shell code."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import struct
import tempfile
import time
import zlib
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from bridge.environment import load_environment_file
from bridge.miniapp_config import load_miniapp_config
from bridge.self_update import _parse_public_signer_policy
from bridge.settings import AppSettings, load_app_settings, validate_app_settings

_MANAGED = "# Managed by SillyTavern Bridge install.sh"


def _new_file(path: Path, raw: bytes) -> bool:
    if path.is_symlink():
        raise ValueError("Refusing a symbolic-link installation target")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except FileExistsError:
        if not path.is_file():
            raise ValueError("Installation target is not a regular file") from None
        return False
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return True


def _atomic_file(path: Path, raw: bytes) -> None:
    if path.is_symlink():
        raise ValueError("Refusing a symbolic-link installation target")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".install-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _starter_png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    card = {
        "name": "Guide",
        "description": "A thoughtful fictional guide for an open-ended conversation.",
        "personality": "Curious, considerate, clear-spoken.",
        "scenario": "A quiet meeting place.",
        "first_mes": "Hello. Where would you like our conversation to begin?",
        "mes_example": "",
    }
    scan = b"".join(b"\x00" + bytes((50 + y // 2, 90 + y // 3, 140 + y // 4)) * 64 for y in range(64))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0))
        + chunk(b"tEXt", b"chara\x00" + base64.b64encode(json.dumps(card).encode()))
        + chunk(b"IDAT", zlib.compress(scan))
        + chunk(b"IEND", b"")
    )


def installation_settings(source: Path, home: Path, env_path: Path) -> AppSettings:
    values = {"SILLYTAVERN_ENV_FILE": str(env_path), "SILLYTAVERN_BRIDGE_SOURCE_DIR": str(source)}
    load_environment_file(env_path, values)
    values.setdefault("SILLYTAVERN_DEFAULT_CHARACTER", "starter.png")
    return load_app_settings(values, home=home)


def _unit_value(value: str, *, executable: bool = False) -> str:
    if any(ord(ch) < 32 for ch in value):
        raise ValueError("Control characters are not allowed in installation paths")
    value = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return '"' + (value.replace("$", "$$") if executable else value) + '"'


def _unit(source: Path, settings: AppSettings, env_path: Path) -> str:
    paths = {
        source,
        settings.bridge_home,
        settings.character_dir,
        settings.world_dir,
        settings.native_persona_settings_file.parent,
        settings.native_persona_avatar_dir,
        settings.character_backup_dir,
        settings.native_persona_backup_dir,
    }
    writable = " ".join(_unit_value(str(path)) for path in sorted(paths))
    executable = _unit_value(str(source / ".venv/bin/python"), executable=True)
    launcher = _unit_value(str(source / "sillytavern_telegram_bridge.py"), executable=True)
    return f"""{_MANAGED}
[Unit]
Description=SillyTavern Telegram Bridge and private Mini App
After=network-online.target
[Service]
Type=simple
WorkingDirectory={_unit_value(str(source))}
Environment={_unit_value("SILLYTAVERN_ENV_FILE=" + str(env_path))}
Environment={_unit_value("SILLYTAVERN_BRIDGE_SOURCE_DIR=" + str(source))}
ExecStart={executable} {launcher}
Restart=on-failure
RestartSec=5
TimeoutStopSec=90
NoNewPrivileges=true
UMask=0077
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths={writable} -%h/.cache/huggingface
RestrictSUIDSGID=true
LockPersonality=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
TasksMax=128
[Install]
WantedBy=default.target
"""


def _provider_document(settings: AppSettings) -> bytes | None:
    endpoint = settings.environ.get("SILLYTAVERN_PROVIDER_ENDPOINT", "").strip()
    selection = settings.default_model.split("::")
    if not endpoint or len(selection) != 2 or not all(selection):
        return None
    provider, model = selection
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", provider):
        raise ValueError("Provider ID must contain letters, numbers, hyphens or underscores")
    parts = urlsplit(endpoint)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Provider endpoint must be an HTTP(S) URL without credentials")
    models = [model, *[x.strip() for x in settings.environ.get("SILLYTAVERN_EXTRA_MODELS", "").split(",") if x.strip()]]
    if len(models) > 500 or any(len(x) > 200 or any(c.isspace() for c in x) for x in models):
        raise ValueError("Invalid configured model identifiers")
    transport = settings.environ.get("SILLYTAVERN_PROVIDER_TRANSPORT", "chat_completions").strip()
    if transport not in {
        "chat_completions",
        "openai",
        "openai_compatible",
        "anthropic_messages",
        "opencode_muse",
        "openai_codex",
    }:
        raise ValueError("Unsupported provider transport")
    payload = {
        "providers": {
            provider: {
                "transport": transport,
                "api_endpoint": endpoint,
                "api_key_env": "LLM_API_KEY",
                "models": list(dict.fromkeys(models)),
                "discover_models": False,
            }
        }
    }
    return (_MANAGED + "\n" + yaml.safe_dump(payload, sort_keys=False)).encode()


def prepare_install(source: Path, home: Path, env_path: Path, unit_dir: Path, *, replace_unit: bool = False) -> dict:
    source, home, env_path, unit_dir = (Path(p).absolute() for p in (source, home, env_path, unit_dir))
    if env_path.is_symlink():
        raise ValueError("Environment file must not be a symbolic link")
    template = (source / ".env.example").read_text(encoding="utf-8")
    template = template.replace(
        "SILLYTAVERN_DEFAULT_CHARACTER=example-character.png", "SILLYTAVERN_DEFAULT_CHARACTER=starter.png"
    )
    template = template.replace(
        "SILLYTAVERN_MODEL=example-provider::example-model", "SILLYTAVERN_MODEL=default::replace-model"
    )
    _new_file(env_path, template.encode())
    settings = installation_settings(source, home, env_path)
    config = load_miniapp_config(settings)
    for path in {
        settings.bridge_home,
        settings.db_file.parent,
        settings.log_file.parent,
        settings.character_dir,
        settings.world_dir,
        settings.system_prompts_dir,
        settings.native_persona_avatar_dir,
        settings.character_backup_dir,
        settings.native_persona_backup_dir,
    }:
        if path.is_symlink():
            raise ValueError("Installation directories must not be symbolic links")
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    starter = _starter_png()
    _new_file(settings.character_dir / "starter.png", starter)
    _new_file(settings.native_persona_avatar_dir / "user-default.png", starter)
    _new_file(
        settings.native_persona_settings_file,
        json.dumps(
            {"user_avatar": "user-default.png", "power_user": {"personas": {}, "persona_descriptions": {}}}
        ).encode(),
    )
    manifest_path = env_path.parent / "installer-state.json"
    if manifest_path.is_symlink():
        raise ValueError("Installer state must not be a symbolic link")
    try:
        state = json.loads(manifest_path.read_text())
        if not isinstance(state, dict):
            state = {}
    except (FileNotFoundError, ValueError):
        state = {}
    state = {key: value for key, value in state.items() if key == "provider_hash"}
    provider = _provider_document(settings)
    if provider is not None:
        path = settings.provider_config_file
        existing = path.read_bytes() if path.is_file() and not path.is_symlink() else None
        old_hash = state.get("provider_hash")
        if existing is None:
            if _new_file(path, provider):
                state["provider_hash"] = hashlib.sha256(provider).hexdigest()
        elif old_hash == hashlib.sha256(existing).hexdigest():
            _atomic_file(path, provider)
            state["provider_hash"] = hashlib.sha256(provider).hexdigest()
    key = settings.environ.get("SILLYTAVERN_UPDATE_PUBLIC_KEY", "").strip()
    if key:
        policy = _parse_public_signer_policy(("bridge-release " + key + "\n").encode("ascii"))
        if settings.update_allowed_signers is None:
            raise ValueError("Set SILLYTAVERN_UPDATE_ALLOWED_SIGNERS to an external trust-file path")
        _new_file(settings.update_allowed_signers, policy.render().encode("ascii"))
    unit = unit_dir / "sillytavern-telegram.service"
    if unit.is_symlink():
        raise ValueError("Service file must not be a symbolic link")
    previous = unit.read_text() if unit.is_file() else ""
    managed = not previous or previous.startswith(_MANAGED) or replace_unit
    if managed:
        if previous and not previous.startswith(_MANAGED):
            _new_file(unit.with_suffix(f".service.backup-{time.time_ns()}"), previous.encode())
        _atomic_file(unit, _unit(source, settings, env_path).encode())
    state.update(
        {
            "format": 1,
            "unit_managed": managed,
            "unit_path": str(unit),
            "env_path": str(env_path),
            "public_url": config.public_url,
            "source": str(source),
        }
    )
    _atomic_file(manifest_path, (json.dumps(state, indent=2) + "\n").encode())
    return state


def validate_install(source: Path, home: Path, env_path: Path) -> list[str]:
    from bridge.codex_auth import DEFAULT_CODEX_BASE_URL, auth_status
    from bridge.model_router import ModelRouter
    from bridge.network_security import validate_provider_endpoint
    from bridge.provider_catalog import load_routing_catalog

    settings = installation_settings(source, home, env_path)
    missing = [
        key
        for key in ("SILLYTAVERN_TELEGRAM_BOT_TOKEN", "SILLYTAVERN_TELEGRAM_ALLOWED_USERS", "SILLYTAVERN_MODEL")
        if not settings.environ.get(key, "").strip()
    ]
    if "replace-model" in settings.default_model:
        missing.append("SILLYTAVERN_MODEL")
    if missing:
        return missing
    try:
        validate_app_settings(settings)
        load_miniapp_config(settings)
        route = ModelRouter(load_catalog=lambda: load_routing_catalog(app_settings=settings)).route(
            settings.default_model
        )
        transport = str(route.spec.get("transport") or "chat_completions")
        endpoint = str(route.spec.get("api_endpoint") or route.spec.get("api") or "")
        if not endpoint and transport == "openai_codex":
            endpoint = DEFAULT_CODEX_BASE_URL
        validate_provider_endpoint(endpoint, environ=settings.environ)
        if transport == "openai_codex":
            if not auth_status(settings.codex_oauth_file)["authenticated"]:
                return ["OpenAI Codex OAuth login is missing. Run --codex-login."]
        else:
            key_env = str(route.spec.get("api_key_env") or "LLM_API_KEY")
            if transport != "opencode_muse" and not settings.environ.get(key_env):
                return ["The provider credential is missing. Set the configured credential in the private .env file."]
    except Exception:
        return ["Check the default character, provider endpoint/model/key and explicit provider allowed-hosts values."]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "check", "unit-managed"))
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--unit-dir", type=Path)
    parser.add_argument("--replace-unit", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            result = prepare_install(
                args.source,
                args.home,
                args.env,
                args.unit_dir or args.home / ".config/systemd/user",
                replace_unit=args.replace_unit,
            )
            print("Configuration: " + result["env_path"])
            if not result["unit_managed"]:
                print("Existing custom service preserved; use --replace-service to replace it with a backup.")
        elif args.action == "check":
            missing = validate_install(args.source, args.home, args.env)
            if missing:
                print("Fill or correct these .env settings: " + ", ".join(missing))
                return 2
            print("Configuration checks passed.")
        else:
            state = json.loads((args.env.parent / "installer-state.json").read_text())
            return 0 if state["unit_managed"] else 2
        return 0
    except Exception as exc:
        print(
            "Installation configuration could not be prepared ("
            + type(exc).__name__
            + "). Check path ownership, permissions and .env values."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
