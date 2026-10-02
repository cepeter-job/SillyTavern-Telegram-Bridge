import os
from pathlib import Path

import pytest

import bridge.background as _background

_PRODUCTION_MAX_XDIST_WORKERS = 2


def _production_host_marker() -> Path:
    configured_home = os.environ.get("SILLYTAVERN_BRIDGE_HOME", "").strip()
    if configured_home:
        bridge_home = Path(configured_home).expanduser()
    else:
        bridge_home = Path.home() / ".local/share/sillytavern-telegram"
    return bridge_home / "PRODUCTION_HOST"


def _production_test_worker_limit() -> int | None:
    return _PRODUCTION_MAX_XDIST_WORKERS if _production_host_marker().is_file() else None


def pytest_xdist_auto_num_workers(config):
    del config
    return _production_test_worker_limit()


def pytest_configure(config):
    limit = _production_test_worker_limit()
    if limit is None:
        return
    requested = getattr(getattr(config, "option", None), "numprocesses", None)
    if isinstance(requested, str) and requested.isdigit():
        requested = int(requested)
    if isinstance(requested, int) and requested > limit:
        raise pytest.UsageError(
            f"Production host allows at most {limit} pytest-xdist workers; run larger parallel suites in CI."
        )


def pytest_sessionfinish(session, exitstatus):
    """Do not let async utility work leak past the pytest process lifetime."""
    _background.begin_background_shutdown()
    if not _background.drain_background_jobs(timeout=15.0):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.fixture
def app_settings_builder():
    from settings_test_support import SettingsBuilder

    return SettingsBuilder()


@pytest.fixture(autouse=True)
def reject_external_test_connections(monkeypatch):
    """External calls must be mocked, even if application code catches failures."""
    import socket

    from network_test_support import guard_connect

    attempts = []
    monkeypatch.setattr(socket.socket, "connect", guard_connect(socket.socket.connect, attempts))
    monkeypatch.setattr(socket.socket, "connect_ex", guard_connect(socket.socket.connect_ex, attempts))
    yield
    assert not attempts, "Unexpected external socket attempt during test; provide an explicit transport mock"
