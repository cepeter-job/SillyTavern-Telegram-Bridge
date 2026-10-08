"""Installer-only update trust path migration; never source user .env values."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bridge.environment import load_environment_file
from bridge.install_support import _expand_install_path, configure_environment


def ensure_install_update_signer_path(env_path: Path, home: Path, preferred_signers: Path) -> Path:
    """Fill only missing update signer paths; preserve custom user choices."""
    values: dict[str, str] = {}
    load_environment_file(Path(env_path), values)
    configured = values.get("SILLYTAVERN_UPDATE_ALLOWED_SIGNERS", "").strip()
    if configured:
        return _expand_install_path(configured, home=Path(home))
    preferred = _expand_install_path(str(preferred_signers), home=Path(home))
    configure_environment(Path(env_path), {"SILLYTAVERN_UPDATE_ALLOWED_SIGNERS": str(preferred)})
    return preferred


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("signer-path",))
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--signers-path", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(ensure_install_update_signer_path(args.env, args.home, args.signers_path))
    except Exception:
        print("Could not safely configure the update signer path; check the private .env file.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
