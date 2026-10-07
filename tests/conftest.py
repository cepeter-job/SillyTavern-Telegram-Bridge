import os
from pathlib import Path

import pytest

import bridge.background as _background

_PRODUCTION_MAX_XDIST_WORKERS = 2
_PRODUCTION_MAX_COLLECTED_TESTS = 512
_PRODUCTION_FULL_SUITE_OVERRIDE = "SILLYTAVERN_ALLOW_PRODUCTION_FULL_TESTS"


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


def pytest_collection_modifyitems(config, items):
    del config
    if _production_test_worker_limit() is None:
        return
    override = os.environ.get(_PRODUCTION_FULL_SUITE_OVERRIDE, "").strip().casefold()
    if override in {"1", "true", "yes", "on"}:
        return
    if len(items) > _PRODUCTION_MAX_COLLECTED_TESTS:
        raise pytest.UsageError(
            "production host collected more than 512 tests; run the full suite in CI or set "
            "SILLYTAVERN_ALLOW_PRODUCTION_FULL_TESTS=1 explicitly."
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
    import http.client
    import socket

    import httpx
    from network_test_support import guard_connect, guard_http_request, guard_httpx_send

    attempts = []
    monkeypatch.setattr(socket.socket, "connect", guard_connect(socket.socket.connect, attempts))
    monkeypatch.setattr(socket.socket, "connect_ex", guard_connect(socket.socket.connect_ex, attempts))
    monkeypatch.setattr(
        http.client.HTTPConnection, "putrequest", guard_http_request(http.client.HTTPConnection.putrequest, attempts)
    )
    monkeypatch.setattr(httpx.Client, "send", guard_httpx_send(httpx.Client.send, attempts))
    monkeypatch.setattr(httpx.AsyncClient, "send", guard_httpx_send(httpx.AsyncClient.send, attempts))
    yield
    assert not attempts, "Unexpected external socket attempt during test; provide an explicit transport mock"
