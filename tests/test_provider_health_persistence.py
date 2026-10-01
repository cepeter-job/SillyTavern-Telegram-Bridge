"""Bounded, private runtime history survives restarts without resurrecting probe locks."""

from __future__ import annotations

import importlib
import importlib.util
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
import yaml
from test_provider_runtime_health import Clock

from bridge.provider_errors import ProviderRequestError
from bridge.provider_runtime_health import ProviderRuntimeHealth
from bridge.settings import load_app_settings


def store(path, clock):
    name = "bridge.provider_health_store"
    assert importlib.util.find_spec(name) is not None, "private provider health store is missing"
    return importlib.import_module(name).JsonProviderHealthStore(path, clock=clock)


def fail(health, category="timeout", provider="alpha", model="one", status=None):
    attempt = health.begin(provider, model)
    health.fail(attempt, ProviderRequestError(f"{provider}::{model}", category, status))


def test_private_store_restores_circuit_history_but_not_half_open_reservations(tmp_path):
    clock = Clock()
    path = tmp_path / "provider-health.json"
    health = ProviderRuntimeHealth(clock=clock, store=store(path, clock))
    for _ in range(3):
        fail(health)
    assert path.stat().st_mode & 0o777 == 0o600
    restored = ProviderRuntimeHealth(clock=clock, store=store(path, clock))
    assert restored.snapshot("alpha").state == "cooldown"
    assert len(restored.history("alpha")) == 3
    with pytest.raises(ProviderRequestError):
        restored.begin("alpha", "one")
    clock.now += 60
    restored.begin("alpha", "one")
    restarted = ProviderRuntimeHealth(clock=clock, store=store(path, clock))
    attempt = restarted.begin("alpha", "one")
    restarted.succeed(attempt)
    assert restarted.snapshot("alpha").state == "healthy"
    assert restarted.history("alpha")[-1].state == "healthy"


def test_history_is_bounded_sanitized_and_retained_for_model_failures(tmp_path):
    clock = Clock()
    path = tmp_path / "health.json"
    health = ProviderRuntimeHealth(clock=clock, store=store(path, clock))
    for _ in range(12):
        fail(health)
        clock.now += 1
        health.succeed(health.begin("alpha", "one"))
        clock.now += 1
    fail(health, "model_unavailable", status=404)
    assert len(health.history("alpha")) == 10
    assert health.history("alpha", "one")[-1].category == "model_unavailable"
    content = path.read_text()
    payload = json.loads(content)
    assert payload["version"] == 1
    assert set(payload) == {"version", "records"}
    for record in payload["records"]:
        assert set(record) == {"snapshot", "history", "updated_at"}
        assert len(record["history"]) <= 10
        assert not ({"prompt", "api_key", "endpoint", "error_body"} & set(record["snapshot"]))
    assert not list(tmp_path.glob(".provider-health-*"))


def test_expired_observations_are_unknown_both_in_memory_and_after_restart(tmp_path):
    clock = Clock()
    path = tmp_path / "health.json"
    health = ProviderRuntimeHealth(clock=clock, store=store(path, clock))
    fail(health, "authentication", status=401)
    clock.now += 86401
    assert health.snapshot("alpha").state == "unknown"
    assert health.history("alpha") == ()
    restored = ProviderRuntimeHealth(clock=clock, store=store(path, clock))
    assert restored.snapshot("alpha").state == "unknown"
    health.succeed(health.begin("alpha", "one"))
    assert health.snapshot("alpha").state == "healthy"


@pytest.mark.parametrize("payload", [b"not json", b"[]", b'{"version":99,"records":[]}', b"x" * 4_000_001])
def test_corrupt_unknown_version_or_oversized_store_is_ignored(tmp_path, payload):
    clock = Clock()
    path = tmp_path / "health.json"
    path.write_bytes(payload)
    health = ProviderRuntimeHealth(clock=clock, store=store(path, clock))
    assert health.snapshot("alpha").state == "unknown"
    health.succeed(health.begin("alpha", "one"))
    assert health.snapshot("alpha").state == "healthy"
    assert json.loads(path.read_text())["version"] == 1


def test_load_and_save_failures_do_not_mask_original_request_or_success():
    class BrokenStore:
        def load(self):
            raise OSError("private path")

        def save(self, records):
            raise OSError("private disk error")

    health = ProviderRuntimeHealth(clock=Clock(), store=BrokenStore())
    fail(health, "authentication", status=401)
    assert health.snapshot("alpha").state == "auth_error"
    health.reset("alpha")
    health.succeed(health.begin("alpha", "one"))
    assert health.snapshot("alpha").state == "healthy"


def test_reset_is_persisted_and_old_inflight_failure_cannot_undo_it(tmp_path):
    clock = Clock()
    path = tmp_path / "health.json"
    health = ProviderRuntimeHealth(clock=clock, store=store(path, clock))
    old = health.begin("alpha", "two")
    fail(health, "authentication", status=401)
    health.reset("alpha")
    health.fail(old, ProviderRequestError("alpha::two", "authentication", 401))
    restarted = ProviderRuntimeHealth(clock=clock, store=store(path, clock))
    assert restarted.snapshot("alpha").state == "unknown"
    assert restarted.history("alpha")[-1].state == "unknown"


def test_runtime_entries_and_store_are_bounded_without_affecting_new_calls(tmp_path):
    clock = Clock()
    health = ProviderRuntimeHealth(clock=clock)
    for i in range(1100):
        health.succeed(health.begin(f"p{i}", "model"))
        clock.now += 1
    assert hasattr(health, "records"), "bounded diagnostic snapshots are missing"
    records = health.records()
    assert len(records) <= 1024
    assert health.snapshot("p1099").state == "healthy"
    assert health.snapshot("p0").state == "unknown"
    adapter = store(tmp_path / "health.json", clock)
    adapter.save(records)
    assert len(adapter.load()) <= 1024


def test_concurrent_observations_leave_valid_atomic_file_and_no_lost_latest_state(tmp_path):
    clock = Clock()
    path = tmp_path / "health.json"
    health = ProviderRuntimeHealth(clock=clock, store=store(path, clock))
    barrier = threading.Barrier(6)

    def success(index):
        barrier.wait(timeout=5)
        health.succeed(health.begin(f"p{index}", "model"))

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(success, range(6)))
    restored = ProviderRuntimeHealth(clock=clock, store=store(path, clock))
    for i in range(6):
        assert restored.snapshot(f"p{i}").state == "healthy"
    assert path.stat().st_mode & 0o777 == 0o600


def test_invalid_store_record_does_not_hide_valid_neighbors_or_restore_forever_block(tmp_path):
    clock = Clock()
    path = tmp_path / "health.json"
    adapter = store(path, clock)
    health = ProviderRuntimeHealth(clock=clock, store=adapter)
    fail(health, "authentication", status=401)
    payload = json.loads(path.read_text())
    valid = payload["records"][0]
    injected = json.loads(json.dumps(valid))
    injected["snapshot"]["provider_id"] = "bad"
    injected["snapshot"]["cooldown_until"] = float("inf")
    injected["snapshot"]["last_category"] = "private raw error body"
    payload["records"].insert(0, injected)
    path.write_text(json.dumps(payload))
    restored = ProviderRuntimeHealth(clock=clock, store=adapter)
    assert restored.snapshot("alpha").state == "auth_error"
    assert restored.snapshot("bad").state == "unknown"
    assert "private" not in repr(restored.records())


def probe_service(tmp_path, count=7):
    name = "bridge.provider_probe_service"
    assert importlib.util.find_spec(name) is not None, "bounded manual probe service is missing"
    module = importlib.import_module(name)
    settings = load_app_settings({}, home=tmp_path)
    settings.provider_config_file.parent.mkdir(parents=True)
    settings.provider_config_file.write_text(
        yaml.safe_dump({"providers": {f"p{i}": {"name": f"Provider {i}", "models": ["model"]} for i in range(count)}})
    )
    return module, module.ProviderProbeService(
        app_settings=settings, probe_backend=lambda *a: pytest.fail("probe backend not supplied")
    )


def test_manual_probe_sweep_uses_at_most_three_workers_and_returns_catalog_order(tmp_path, monkeypatch):
    _module, service = probe_service(tmp_path)
    gate = threading.Barrier(3)
    lock = threading.Lock()
    active = high = arrived = 0

    def probe(provider, spec, **kwargs):
        nonlocal active, high, arrived
        with lock:
            active += 1
            arrived += 1
            first_batch = arrived <= 3
            high = max(high, active)
        if first_batch:
            gate.wait(timeout=5)
        with lock:
            active -= 1
        return provider, spec["name"], "catalog reachable"

    monkeypatch.setattr(service, "_probe_backend", probe)
    result = service.check()
    assert high == 3
    assert [row[0] for row in result] == [f"p{i}" for i in range(7)]


def test_manual_sweeps_are_serialized_per_service(tmp_path, monkeypatch):
    _module, service = probe_service(tmp_path, count=1)
    entered = threading.Event()
    release = threading.Event()
    both_submitted = threading.Barrier(3)
    lock = threading.Lock()
    active = high = 0

    def probe(provider, spec, **kwargs):
        nonlocal active, high
        with lock:
            active += 1
            high = max(active, high)
        entered.set()
        assert release.wait(5)
        with lock:
            active -= 1
        return provider, spec["name"], "catalog reachable"

    monkeypatch.setattr(service, "_probe_backend", probe)

    def run():
        both_submitted.wait(timeout=5)
        return service.check()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run)
        second = pool.submit(run)
        both_submitted.wait(timeout=5)
        assert entered.wait(5)
        release.set()
        assert first.result(timeout=5) == second.result(timeout=5)
    assert high == 1


def test_manual_probe_failures_are_isolated_and_targeting_is_exact(tmp_path, monkeypatch):
    _module, service = probe_service(tmp_path, count=3)
    calls = []

    def probe(provider, spec, **kwargs):
        calls.append(provider)
        if provider == "p1":
            raise ValueError("private unexpected adapter error")
        return provider, spec["name"], "catalog reachable"

    monkeypatch.setattr(service, "_probe_backend", probe)
    rows = service.check()
    assert len(rows) == 3
    assert "private" not in repr(rows)
    assert "failed" in rows[1][2]
    calls.clear()
    assert service.check("p2") == [("p2", "Provider 2", "catalog reachable")]
    assert calls == ["p2"]


def test_application_composes_private_store_and_per_instance_manual_probe_service(tmp_path):
    from bridge.main import _build_startup_services
    from bridge.model_router import ModelRouter

    clock = Clock()
    store(tmp_path / "health.json", clock)
    settings = load_app_settings({}, home=tmp_path)
    first = _build_startup_services(settings, model_router=ModelRouter(lambda: {}))
    second = _build_startup_services(settings, model_router=ModelRouter(lambda: {}))
    assert first.provider_probes is not None
    assert first.provider_probes is not second.provider_probes
    first.provider.policy.health.succeed(first.provider.policy.health.begin("alpha", "one"))
    assert (settings.bridge_home / "provider_health.json").is_file()


def test_persisting_other_provider_does_not_release_active_half_open_claim(tmp_path):
    clock = Clock()
    health = ProviderRuntimeHealth(clock=clock, store=store(tmp_path / "health.json", clock))
    for _ in range(3):
        fail(health)
    clock.now += 60
    probe = health.begin("alpha", "one")
    health.succeed(health.begin("beta", "one"))
    assert health.snapshot("alpha").state == "half_open"
    with pytest.raises(ProviderRequestError) as error:
        health.begin("alpha", "two")
    assert error.value.blocked
    health.succeed(probe)
    assert health.snapshot("alpha").state == "healthy"
