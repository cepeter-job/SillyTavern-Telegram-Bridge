"""Explicit boot identity and observed polling health, without exposing configuration."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from bridge.self_update import APPLICATION, MARKER
from bridge.settings import AppSettings
from bridge.subprocess_security import minimal_subprocess_environment


@dataclass(frozen=True)
class DeploymentIdentity:
    version: str = "unknown"
    commit: str = ""


def capture_deployment(settings: AppSettings) -> DeploymentIdentity:
    """Read the loaded package's root, never an unrelated installed mirror."""
    root = Path(__file__).resolve().parents[1]
    try:
        if root == settings.update_live_dir.resolve():
            marker = root / MARKER
            if marker.is_symlink() or marker.stat().st_size > 4096:
                return DeploymentIdentity()
            data = json.loads(marker.read_text())
            if data.get("application") != APPLICATION or data.get("format") != 1:
                return DeploymentIdentity()
            version, commit = str(data.get("version", "")), str(data.get("commit", ""))
        elif root == settings.update_repo_dir.resolve():
            git = shutil.which("git")
            if not git:
                return DeploymentIdentity()
            result = subprocess.run(  # noqa: S603 -- fixed git executable/arguments, trusted loaded package root
                [git, "-C", str(root), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
                env=minimal_subprocess_environment(),
            )
            commit = result.stdout.strip()
            status = subprocess.run(  # noqa: S603 -- fixed read-only git command, never a shell
                [git, "-c", "core.fsmonitor=false", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
                env=minimal_subprocess_environment(),
            )
            if status.stdout.strip():
                return DeploymentIdentity()

            text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
            versions = re.findall(r"^## \[([0-9]+\.[0-9]+\.[0-9]+)\]", text, re.MULTILINE)
            version = versions[0] if versions else "unknown"
        else:
            return DeploymentIdentity()
        if not re.fullmatch(r"[0-9]{1,5}\.[0-9]{1,5}\.[0-9]{1,5}", version) or not re.fullmatch(
            r"[0-9a-f]{40}", commit
        ):
            return DeploymentIdentity()
        return DeploymentIdentity(version, commit)
    except (OSError, ValueError, AttributeError, subprocess.SubprocessError):
        return DeploymentIdentity()


class RuntimeHealth:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.deployment = DeploymentIdentity()
        self.started_at = 0.0
        self._started_clock = 0.0
        self.last_success = 0.0
        self.state = "unobserved"

    def begin(self, settings: AppSettings) -> None:
        with self._lock:
            if self.started_at:
                return
            self.deployment = capture_deployment(settings)
            self.started_at = time.time()
            self._started_clock = time.monotonic()
            self.state = "starting"

    def poll_succeeded(self) -> None:
        with self._lock:
            self.last_success = time.time()
            self.state = "polling"

    def poll_failed(self) -> None:
        with self._lock:
            self.state = "degraded"

    def stopping(self) -> None:
        with self._lock:
            self.state = "stopping"

    def snapshot(self) -> dict:
        with self._lock:
            state = self.state
            if state == "polling" and time.time() - self.last_success > 120:
                state = "degraded"
            return {
                "deployment": asdict(self.deployment),
                "started_at": self.started_at,
                "uptime_seconds": int(time.monotonic() - self._started_clock) if self._started_clock else 0,
                "telegram": {"state": state, "last_success": self.last_success},
            }
