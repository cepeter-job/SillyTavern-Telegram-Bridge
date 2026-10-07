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
def fixture_process():
    root = Path(__file__).resolve().parents[1]
    child = subprocess.Popen(
        [sys.executable, "tests/miniapp_browser_fixture.py"],
        cwd=root,
        env={**os.environ, "MINIAPP_FIXTURE_RECOVERY": "1"},
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


def request(ready, path, *, body=None, authorized=False, discard=False):
    headers = {"Authorization": "tma " + ready["initData"]} if authorized else {}
    if body is not None:
        headers["Content-Type"] = "application/json"
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
        assert result["state"] == "succeeded", result
        assert "Synthetic lighthouse continuity" in result["result"]["summary"]
        _, repeated = request(ready, "/api/v1/memory/summary/generate", body=values, authorized=True)
        assert repeated["id"] == result["id"]
        assert request(ready, "/fixture/metrics")[1] == {
            "handlers": 1,
            "providers": 1,
            "jobs": [{"id": result["id"], "operation_id": "fixture-lost-response", "state": "succeeded"}],
        }
