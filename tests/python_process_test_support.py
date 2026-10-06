"""Fresh interpreter probes for import and startup boundary tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def run_python(source: str) -> subprocess.CompletedProcess[str]:
    """Keep each import probe isolated from the test runner's application graph."""
    return subprocess.run(
        [sys.executable, "-c", source],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
