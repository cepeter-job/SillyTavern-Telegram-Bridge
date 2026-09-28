"""Minimal environment construction for helper subprocesses."""

from __future__ import annotations

import os
from collections.abc import Mapping

_SAFE_ENVIRONMENT_KEYS = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "TMPDIR",
    "TEMP",
    "TMP",
    "SystemRoot",
    "WINDIR",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
)


def minimal_subprocess_environment(
    environ: Mapping[str, str] | None = None,
    *,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return only non-secret process plumbing required by trusted helpers."""
    source = os.environ if environ is None else environ
    result = {key: str(source[key]) for key in _SAFE_ENVIRONMENT_KEYS if key in source}
    if extra:
        result.update({str(key): str(value) for key, value in extra.items()})
    return result
