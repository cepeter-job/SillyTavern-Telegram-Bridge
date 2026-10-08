"""Runtime logging and private-path permissions for explicit application settings."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from bridge.diagnostic_events import configure_identity
from bridge.diagnostic_logging import (
    DiagnosticConsoleHandler,
    DiagnosticFormatter,
    PrivateRotatingHandler,
    configured_secrets,
    logging_options,
)
from bridge.settings import AppSettings


def configure_logging(log_file: Path | None = None, *, app_settings: AppSettings) -> None:
    """Install bounded private JSON logging and a matching systemd stream."""
    target = Path(log_file if log_file is not None else app_settings.log_file).expanduser().absolute()
    root = logging.getLogger()
    if any(
        isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename) == target for handler in root.handlers
    ):
        return
    environ = app_settings.environ
    max_bytes, backups, level = logging_options(environ)
    configure_identity(app_settings.bot_token)
    formatter = DiagnosticFormatter((*configured_secrets(environ), app_settings.bot_token))
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handler = PrivateRotatingHandler(str(target), maxBytes=max_bytes, backupCount=backups)
    handler.setFormatter(formatter)
    root.addHandler(handler)
    if not any(getattr(item, "bridge_console", False) for item in root.handlers):
        console = DiagnosticConsoleHandler()
        console.setFormatter(formatter)
        root.addHandler(console)
    root.setLevel(level)


def enforce_runtime_permissions(*, app_settings: AppSettings) -> None:
    private_dirs = {
        app_settings.db_file.parent,
        app_settings.log_file.parent,
        app_settings.bridge_home / "backups",
        app_settings.character_backup_dir,
    }
    enforce_prompt_permissions = app_settings.enforce_prompt_permissions
    if app_settings.system_prompts_dir.exists() and (
        enforce_prompt_permissions or app_settings.system_prompts_dir.is_relative_to(app_settings.bridge_home.parent)
    ):
        private_dirs.add(app_settings.system_prompts_dir)
    for directory in private_dirs:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            directory.chmod(0o700)
        except OSError:
            logging.warning("Could not protect runtime directory %s", directory, exc_info=True)
    private_files = {
        app_settings.environment_file,
        app_settings.db_file,
        app_settings.log_file,
        app_settings.provider_config_file,
        app_settings.model_cache_file,
    }
    if app_settings.system_prompts_dir.exists() and (
        enforce_prompt_permissions or app_settings.system_prompts_dir.is_relative_to(app_settings.bridge_home.parent)
    ):
        private_files.update(app_settings.system_prompts_dir.glob("*.txt"))
        private_files.update(app_settings.system_prompts_dir.glob("*.json"))
    private_files.update(app_settings.db_file.parent.glob(app_settings.db_file.name + "-*"))
    for path in private_files:
        try:
            if path.is_file() and not path.is_symlink():
                path.chmod(0o600)
        except OSError:
            logging.warning("Could not protect runtime file %s", path, exc_info=True)
