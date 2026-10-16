"""The browser fixture executes the real summary path once, without external I/O."""

import concurrent.futures
import json
import os
import subprocess
import sys
import time
from contextlib import closing, contextmanager
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlsplit

import pytest


@contextmanager
def fixture_process(*, rate_clock=False):
    root = Path(__file__).resolve().parents[1]
    child = subprocess.Popen(
        [sys.executable, "tests/miniapp_browser_fixture.py"],
        cwd=root,
        env={
            **os.environ,
            "MINIAPP_FIXTURE_RECOVERY": "1",
            "MINIAPP_FIXTURE_RATE_CLOCK": "1" if rate_clock else "0",
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    reader = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        ready = json.loads(reader.submit(child.stdout.readline).result(timeout=15))
        yield ready
    finally:
        child.terminate()
        try:
            _, stderr = child.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            child.kill()
            _, stderr = child.communicate()
            pytest.fail("Fixture did not drain and exit after SIGTERM: " + stderr)
        finally:
            reader.shutdown(wait=True)
        assert child.returncode == 0, stderr


def request(ready, path, *, body=None, authorized=False, discard=False, extra_headers=None):
    headers = {"Authorization": "tma " + ready["initData"]} if authorized else {}
    if body is not None:
        headers["Content-Type"] = "application/json"
    headers.update(extra_headers or {})
    endpoint = urlsplit(ready["url"])
    assert endpoint.scheme == "http" and endpoint.hostname == "127.0.0.1"
    with closing(HTTPConnection(endpoint.hostname, endpoint.port, timeout=10)) as connection:
        connection.request(
            "GET" if body is None else "POST",
            path,
            headers=headers,
            body=None if body is None else json.dumps(body).encode(),
        )
        with connection.getresponse() as response:
            return response.status, None if discard else json.load(response)


def test_browser_fixture_runs_one_real_summary_handler_and_synthetic_provider():
    # Break caught: browser evidence silently bypassing the actual summary handler
    # or utility executor, or fixture recovery enqueueing a second model request.
    with fixture_process() as ready:
        status, before = request(ready, "/fixture/metrics")
        assert status == 200
        assert before == {"handlers": 0, "providers": 0, "jobs": []}
        values = {"operation_id": "fixture-lost-response", "session_id": "default"}
        # Accept the HTTP response, deliberately discard its JSON and job ID.
        assert request(ready, "/api/v1/memory/summary/generate", body=values, authorized=True, discard=True)[0] == 200
        assert request(ready, "/fixture/release", body={})[0] == 200
        deadline = time.monotonic() + 10
        while True:
            status, result = request(ready, "/api/v1/jobs/by-operation/fixture-lost-response", authorized=True)
            assert status == 200
            if result["state"] not in {"queued", "running"}:
                break
            assert time.monotonic() < deadline, result
            # Stay below the real authenticated route's 120-requests/minute cap.
            time.sleep(0.1)
        assert result["state"] == "succeeded", result
        assert "Synthetic lighthouse continuity" in result["result"]["summary"]
        _, repeated = request(ready, "/api/v1/memory/summary/generate", body=values, authorized=True)
        assert repeated["id"] == result["id"]
        assert request(ready, "/fixture/metrics")[1] == {
            "handlers": 1,
            "providers": 1,
            "jobs": [{"id": result["id"], "operation_id": "fixture-lost-response", "state": "succeeded"}],
        }


def test_fixture_clock_preserves_auth_rate_limit_and_next_window_recovery():
    # Control changes only the isolated fixture's HTTP clock, never auth, the
    # production 120-request threshold or the process-wide clock/timer functions.
    with fixture_process(rate_clock=True) as ready:
        control = ready.get("rate_clock")
        assert control is not None, "Opt-in fixture clock control is missing"
        assert request(ready, "/api/v1/me")[0] == 401
        for _ in range(120):
            assert request(ready, "/api/v1/me", authorized=True)[0] == 200
        status, result = request(ready, "/api/v1/me", authorized=True)
        assert status == 429 and result["error"]["code"] == "rate_limit"
        assert request(ready, control["path"], body={})[0] == 403
        assert request(ready, control["path"], body={}, extra_headers={"X-Fixture-Rate-Clock": "wrong"})[0] == 403
        assert request(ready, "/api/v1/me", authorized=True)[0] == 429
        status, result = request(
            ready, control["path"], body={}, extra_headers={"X-Fixture-Rate-Clock": control["key"]}
        )
        assert status == 200 and result == {"advanced_seconds": 61}
        for _ in range(120):
            assert request(ready, "/api/v1/me", authorized=True)[0] == 200
        assert request(ready, "/api/v1/me", authorized=True)[0] == 429


def test_fixture_rate_clock_control_requires_explicit_opt_in():
    with fixture_process() as ready:
        assert "rate_clock" not in ready
        assert request(ready, "/fixture/rate-window", body={})[0] == 404
        assert request(ready, "/api/v1/me", authorized=True)[0] == 200
