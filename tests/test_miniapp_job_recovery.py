"""Recovery must find an existing actor-owned job, never admit replacement work."""

import asyncio
from dataclasses import replace

import pytest
from aiohttp.test_utils import TestClient, TestServer
from miniapp_test_support import identity, make_services, signed_data


def test_lookup_is_read_only_and_bound_to_the_original_actor(tmp_path, monkeypatch):
    import sqlite3

    import bridge.miniapp_jobs as jobs

    services, who = make_services(tmp_path), identity()
    queued, executions = [], []
    monkeypatch.setattr(jobs, "submit_background", lambda label, work: queued.append(work) or True)
    first = jobs.submit_job(
        services, who, "test", {"operation_id": "lost-response"}, lambda *args: executions.append(1) or {"value": 42}
    )
    queued.pop()()
    original_factory = services.db_factory

    def read_only():
        db = original_factory()
        db.execute("PRAGMA query_only=ON")
        return db

    monkeypatch.setattr(services, "db_factory", read_only)
    recovered = jobs.job_by_operation(services, who, {"operation_id": "lost-response"})
    assert recovered["id"] == first["id"]
    assert recovered["result"] == {"value": 42}
    assert executions == [1] and queued == []
    with pytest.raises(ValueError):
        jobs.job_by_operation(services, identity("67890"), {"operation_id": "lost-response"})
    with pytest.raises(ValueError):
        jobs.job_by_operation(services, who, {"operation_id": "missing"})
    db = original_factory()
    try:
        db.execute("PRAGMA query_only=ON")
        with pytest.raises(sqlite3.OperationalError):
            db.execute("DELETE FROM miniapp_jobs")
    finally:
        db.close()


def test_accepted_http_submission_is_recovered_with_one_worker_execution(tmp_path, monkeypatch):
    import bridge.miniapp_http as http
    import bridge.miniapp_jobs as jobs
    from bridge.miniapp_config import load_miniapp_config
    from bridge.miniapp_types import ApiRoute

    services = make_services(tmp_path)
    services.config = replace(services.config, allowed_users=frozenset({"12345", "67890"}))
    queued, calls = [], []
    monkeypatch.setattr(jobs, "submit_background", lambda label, work: queued.append(work) or True)
    routes = http.api_routes()
    routes.append(ApiRoute("POST", "/test/recovery", lambda *args: calls.append(1) or {"answer": 7}, "recovery-test"))
    monkeypatch.setattr(http, "api_routes", lambda: routes)

    async def run():
        async with TestClient(TestServer(http.create_miniapp_app(services, load_miniapp_config(services.config)))) as client:
            headers = {"Authorization": "tma " + signed_data()}
            values = {"operation_id": "accepted-once", "session_id": "default"}
            response = await client.post("/api/v1/test/recovery", headers=headers, json=values)
            assert response.status == 200
            # Intentionally discard the accepted response instead of learning its job ID.
            response.close()
            assert len(queued) == 1
            queued.pop()()
            found = await client.get("/api/v1/jobs/by-operation/accepted-once", headers=headers)
            assert found.status == 200
            recovered = await found.json()
            assert recovered["state"] == "succeeded" and recovered["result"] == {"answer": 7}
            repeated = await client.post("/api/v1/test/recovery", headers=headers, json=values)
            assert (await repeated.json())["id"] == recovered["id"]
            assert calls == [1] and queued == []
            denied = await client.get(
                "/api/v1/jobs/by-operation/accepted-once",
                headers={"Authorization": "tma " + signed_data(user_id=67890)},
            )
            assert denied.status == 404
            assert (await client.get("/api/v1/jobs/by-operation/accepted-once")).status == 401

    asyncio.run(run())
