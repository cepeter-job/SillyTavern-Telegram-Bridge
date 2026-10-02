from types import SimpleNamespace

import conftest as test_config
import pytest


def _mark_production_host(tmp_path, monkeypatch):
    bridge_home = tmp_path / "bridge-home"
    bridge_home.mkdir()
    (bridge_home / "PRODUCTION_HOST").write_text("production\n", encoding="utf-8")
    monkeypatch.setenv("SILLYTAVERN_BRIDGE_HOME", str(bridge_home))


def test_production_host_caps_xdist_auto_workers_at_two(tmp_path, monkeypatch):
    _mark_production_host(tmp_path, monkeypatch)
    hook = getattr(test_config, "pytest_xdist_auto_num_workers", None)

    assert hook is not None
    assert hook(SimpleNamespace()) == 2


def test_nonproduction_host_preserves_xdist_default(tmp_path, monkeypatch):
    monkeypatch.setenv("SILLYTAVERN_BRIDGE_HOME", str(tmp_path / "bridge-home"))
    hook = getattr(test_config, "pytest_xdist_auto_num_workers", None)

    assert hook is not None
    assert hook(SimpleNamespace()) is None


def test_production_host_rejects_explicit_worker_count_above_two(tmp_path, monkeypatch):
    _mark_production_host(tmp_path, monkeypatch)
    hook = getattr(test_config, "pytest_configure", None)
    config = SimpleNamespace(option=SimpleNamespace(numprocesses=4))

    assert hook is not None
    with pytest.raises(pytest.UsageError, match="at most 2"):
        hook(config)


def test_production_host_allows_two_explicit_workers(tmp_path, monkeypatch):
    _mark_production_host(tmp_path, monkeypatch)
    hook = getattr(test_config, "pytest_configure", None)
    config = SimpleNamespace(option=SimpleNamespace(numprocesses=2))

    assert hook is not None
    hook(config)


def test_production_host_rejects_large_full_suite_collection(tmp_path, monkeypatch):
    _mark_production_host(tmp_path, monkeypatch)
    hook = getattr(test_config, "pytest_collection_modifyitems", None)

    assert hook is not None
    with pytest.raises(pytest.UsageError, match=r"production host.*512"):
        hook(SimpleNamespace(), [object()] * 513)


def test_production_host_allows_targeted_collection_within_limit(tmp_path, monkeypatch):
    _mark_production_host(tmp_path, monkeypatch)
    hook = getattr(test_config, "pytest_collection_modifyitems", None)

    assert hook is not None
    hook(SimpleNamespace(), [object()] * 100)


def test_production_host_large_collection_requires_explicit_override(tmp_path, monkeypatch):
    _mark_production_host(tmp_path, monkeypatch)
    monkeypatch.setenv("SILLYTAVERN_ALLOW_PRODUCTION_FULL_TESTS", "1")
    hook = getattr(test_config, "pytest_collection_modifyitems", None)

    assert hook is not None
    hook(SimpleNamespace(), [object()] * 513)
