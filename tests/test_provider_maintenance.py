"""Maintenance never hides valid routes or turns discovery into inference health."""

from __future__ import annotations

import json
import time
from dataclasses import replace

import pytest
import yaml

from bridge import provider_catalog as catalog
from bridge import provider_discovery as discovery
from bridge.settings import load_app_settings


class Response:
    status = 200

    def __init__(self, payload=None):
        self.body = json.dumps(payload or {"data": [{"id": "remote"}]}).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        data = self.body if size < 0 else self.body[:size]
        self.body = self.body[len(data) :]
        return data


@pytest.fixture
def configured(tmp_path, monkeypatch):
    settings = load_app_settings({"LLM_API_KEY": "fixture-key", "ANTHROPIC_API_KEY": "anthropic-key"}, home=tmp_path)
    settings.provider_config_file.parent.mkdir(parents=True)
    monkeypatch.setattr(discovery, "validate_provider_endpoint", lambda *a, **kw: None)

    def write(providers):
        settings.provider_config_file.write_text(yaml.safe_dump({"providers": providers}, sort_keys=False))
        return settings

    return write


def spec(**options):
    return {
        "api_endpoint": "https://alpha.example/v1",
        "transport": "chat_completions",
        "discover_models": True,
        "models": ["seed"],
        **options,
    }


def test_manual_refresh_targets_one_provider(configured, monkeypatch):
    settings = configured({"alpha": spec(), "beta": spec(api_endpoint="https://beta.example/v1")})
    urls = []
    monkeypatch.setattr(discovery, "strict_urlopen", lambda request, **kw: urls.append(request.full_url) or Response())
    _config, refreshed, failed = discovery.refresh_model_catalog(force=True, provider_id="beta", app_settings=settings)
    assert (refreshed, failed) == (1, 0)
    assert urls == ["https://beta.example/v1/models"]
    assert "alpha" not in json.loads(settings.model_cache_file.read_text())


def test_refresh_isolates_invalid_endpoint_and_retains_other_provider(configured, monkeypatch):
    settings = configured({"bad": spec(api_endpoint="bad-url"), "alpha": spec()})

    def validate(endpoint, **kw):
        if endpoint == "bad-url":
            raise ValueError("private endpoint rejected")

    monkeypatch.setattr(discovery, "validate_provider_endpoint", validate)
    monkeypatch.setattr(discovery, "strict_urlopen", lambda *a, **kw: Response())
    result, refreshed, failed = discovery.refresh_model_catalog(force=True, app_settings=settings)
    assert (refreshed, failed) == (1, 1)
    assert result["providers"]["alpha"]["models"] == ["seed", "remote"]


def test_health_isolates_configuration_error_without_relaxing_validation(configured, monkeypatch):
    settings = configured({"bad": spec(api_endpoint="bad-url"), "alpha": spec()})

    def validate(endpoint, **kw):
        if endpoint == "bad-url":
            raise ValueError("private endpoint rejected")

    monkeypatch.setattr(discovery, "validate_provider_endpoint", validate)
    urls = []
    monkeypatch.setattr(discovery, "strict_urlopen", lambda request, **kw: urls.append(request.full_url) or Response())
    rows = discovery.provider_health_checks(app_settings=settings)
    assert "configuration" in rows[0][2]
    assert "catalog" in rows[1][2]
    assert "private" not in repr(rows)
    assert urls == ["https://alpha.example/v1/models"]


@pytest.mark.parametrize("cache", [[], 3, "wrong", {"alpha": {"models": "not-a-list", "refreshed_at": "wrong"}}])
def test_malformed_cache_cannot_remove_configured_models(configured, monkeypatch, cache):
    settings = configured({"alpha": spec()})
    settings.model_cache_file.write_text(json.dumps(cache))
    monkeypatch.setattr(discovery, "strict_urlopen", lambda *a, **kw: Response())
    groups = discovery.get_model_groups(app_settings=settings)
    assert [label for label, _selection in groups["alpha"][1]] == ["seed", "remote"]


def test_menu_and_router_preserve_the_same_configured_and_discovered_ids(configured, monkeypatch):
    settings = configured({"alpha": spec()})
    monkeypatch.setattr(discovery, "strict_urlopen", lambda *a, **kw: Response())
    groups = discovery.get_model_groups(app_settings=settings)
    assert [label for label, _ in groups["alpha"][1]] == catalog.load_routing_catalog(app_settings=settings)["alpha"][
        "models"
    ]
    assert catalog.load_routing_catalog(app_settings=settings)["alpha"]["models"] == ["seed", "remote"]


def test_static_provider_never_inherits_old_discovery_cache(configured):
    settings = configured({"alpha": spec(discover_models=False, models=[])})
    settings.model_cache_file.write_text(json.dumps({"alpha": {"models": ["old"]}}))
    assert discovery.get_model_groups(app_settings=settings) == {}


def test_model_pagination_is_not_pre_truncated_to_fifty(configured):
    settings = configured({"alpha": spec(discover_models=False, models=[f"m{i}" for i in range(125)])})
    groups = discovery.get_model_groups(app_settings=settings)
    assert len(groups["alpha"][1]) == 125
    assert groups["alpha"][1][-1] == ("m124", "alpha::m124")


def test_empty_discovery_is_reported_and_keeps_last_good_catalog(configured, monkeypatch):
    settings = configured({"alpha": spec()})
    settings.model_cache_file.write_text(json.dumps({"alpha": {"models": ["old"], "refreshed_at": 1}}))
    monkeypatch.setattr(discovery, "strict_urlopen", lambda *a, **kw: Response({"data": []}))
    result, refreshed, failed = discovery.refresh_model_catalog(force=True, app_settings=settings)
    assert (refreshed, failed) == (0, 1)
    assert result["providers"]["alpha"]["models"] == ["seed", "old"]
    cached = json.loads(settings.model_cache_file.read_text())["alpha"]
    assert cached["refreshed_at"] == 1
    assert cached["last_error"] == "empty_catalog"


def test_failed_lazy_refresh_is_not_repeated_on_each_redraw(configured, monkeypatch):
    settings = configured({"alpha": spec()})
    calls = []

    def offline(*args, **kw):
        calls.append(1)
        raise TimeoutError("private")

    monkeypatch.setattr(discovery, "strict_urlopen", offline)
    discovery.get_model_groups(app_settings=settings)
    discovery.get_model_groups(app_settings=settings)
    assert len(calls) == 1
    assert json.loads(settings.model_cache_file.read_text())["alpha"]["last_error"] == "timeout"


def test_read_only_menu_does_not_discover_models(configured, monkeypatch):
    settings = configured({"alpha": spec()})
    monkeypatch.setattr(discovery, "strict_urlopen", lambda *a, **kw: pytest.fail("read-only redraw made a request"))
    assert discovery.get_model_groups(app_settings=settings, refresh=False)["alpha"][1] == [("seed", "alpha::seed")]


def test_discovery_uses_native_anthropic_headers_and_base_endpoint(configured, monkeypatch):
    settings = configured(
        {"alpha": spec(transport="anthropic_messages", api_endpoint="https://alpha.example/v1/messages")}
    )
    seen = []
    monkeypatch.setattr(discovery, "strict_urlopen", lambda request, **kw: seen.append(request) or Response())
    discovery.refresh_model_catalog(force=True, app_settings=settings)
    assert seen[0].full_url == "https://alpha.example/v1/models"
    headers = {key.lower(): value for key, value in seen[0].headers.items()}
    assert headers["x-api-key"] == "anthropic-key"
    assert "authorization" not in headers


def test_first_byte_of_inference_is_not_reported_as_successful_completion(configured, monkeypatch):
    settings = configured({"alpha": spec(health_check="chat_completion")})
    monkeypatch.setattr(discovery, "strict_urlopen", lambda *a, **kw: Response())
    status = discovery.provider_health_checks(app_settings=settings)[0][2]
    assert "stream opened" in status
    assert "not validated" in status


def test_catalog_metadata_reports_staleness_without_removing_routes(configured):
    settings = configured({"alpha": spec()})
    old = time.time() - settings.model_refresh_seconds - 1
    settings.model_cache_file.write_text(
        json.dumps({"alpha": {"models": ["seed", "old"], "refreshed_at": old, "last_error": "timeout"}})
    )
    assert hasattr(catalog, "get_catalog_status"), "catalog freshness metadata is missing"
    status = catalog.get_catalog_status("alpha", app_settings=settings)
    assert (status.configured, status.discovered, status.stale, status.last_error) == (1, 1, True, "timeout")
    assert catalog.load_routing_catalog(app_settings=settings)["alpha"]["models"] == ["seed", "old"]


def test_cache_write_failure_preserves_current_refresh_and_static_routes(configured, monkeypatch, tmp_path):
    settings = configured({"alpha": spec()})
    blocking_file = tmp_path / "file-not-directory"
    blocking_file.write_text("not a directory")
    settings = replace(settings, model_cache_file=blocking_file / "cache.json")
    monkeypatch.setattr(discovery, "strict_urlopen", lambda *a, **kw: Response())
    result, updated, failed = discovery.refresh_model_catalog(force=True, app_settings=settings)
    assert updated == 1
    assert failed == 0
    assert result["providers"]["alpha"]["models"] == ["seed", "remote"]


def test_cache_timestamp_rejects_overflowing_integers():
    from bridge.provider_catalog_cache import cache_timestamp

    assert cache_timestamp(10**400) is None


def test_concurrent_cache_updates_preserve_both_providers_with_private_permissions(configured):
    from concurrent.futures import ThreadPoolExecutor

    from bridge.provider_catalog_cache import read_model_cache, update_model_cache

    settings = configured({"alpha": spec(), "beta": spec()})

    def write(provider):
        update_model_cache({provider: {"models": [provider], "last_attempt_at": 1}}, app_settings=settings)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(write, ["alpha", "beta"]))
    assert set(read_model_cache(app_settings=settings)) == {"alpha", "beta"}
    assert settings.model_cache_file.stat().st_mode & 0o777 == 0o600
    assert not list(settings.model_cache_file.parent.glob(".models-*"))


def test_older_cache_writer_does_not_replace_newer_results(configured):
    from bridge.provider_catalog_cache import read_model_cache, update_model_cache

    settings = configured({"alpha": spec()})
    for timestamp, model in [(20, "new"), (10, "old")]:
        update_model_cache({"alpha": {"models": [model], "last_attempt_at": timestamp}}, app_settings=settings)
    assert read_model_cache(app_settings=settings)["alpha"]["models"] == ["new"]
