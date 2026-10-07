"""A bounded HTTP attempt must include headers, body, cleanup and error semantics."""

import json
import sys
import time
from pathlib import Path

import pytest
from retrieval_http_test_support import wire_server

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


@pytest.mark.parametrize("options", [{"header_delay": 0.3}, {"chunk_size": 1, "chunk_delay": 0.01}])
def test_http_wall_deadline_covers_slow_headers_and_body(options):
    from story_memory_retrieval_http import BoundedHTTP, StudyHTTPError

    with wire_server(lambda call: (200, {"result": "x" * 100}, options)) as (endpoint, calls):
        client = BoundedHTTP(
            endpoint, request_budget=1, phase_limits={"embedding": 1}, deadline_seconds=2, request_timeout=0.08
        )
        start = time.monotonic()
        with pytest.raises(StudyHTTPError) as raised:
            client.request("POST", "", {"input": ["Synthetic text"]}, phase="embedding")
        assert raised.value.category == "timeout"
        assert raised.value.attempted
        assert time.monotonic() - start < 1
        assert len(calls) == 1


def test_http_disables_redirects_proxies_retries_and_sanitizes_errors(monkeypatch):
    from story_memory_retrieval_http import BoundedHTTP, StudyHTTPError

    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    for options, status in (({"redirect": "http://127.0.0.1:1/SECRET"}, 302), ({"disconnect": True}, 200)):
        with wire_server(
            lambda call, status=status, options=options: (status, {"secret": "PRIVATE RESPONSE"}, options)
        ) as (endpoint, calls):
            client = BoundedHTTP(
                endpoint, request_budget=1, phase_limits={"check": 1}, deadline_seconds=2, token="PRIVATE CREDENTIAL"
            )
            with pytest.raises(StudyHTTPError) as raised:
                client.request("GET", "", phase="check")
            assert len(calls) == 1
            assert "PRIVATE" not in str(raised.value)
            assert "PRIVATE" not in json.dumps(client.observations)
            with pytest.raises(StudyHTTPError) as capped:
                client.request("GET", "", phase="check")
            assert not capped.value.attempted
            assert len(calls) == 1


def test_explicit_http_does_not_unlock_other_offline_paths(tmp_path):
    from story_memory_retrieval_corpus import RetrievalRuntime
    from story_memory_retrieval_http import BoundedHTTP

    with wire_server(lambda call: (200, {"ok": True}, {})) as (endpoint, calls):
        client = BoundedHTTP(endpoint, request_budget=1, phase_limits={"check": 1}, deadline_seconds=2)
        with RetrievalRuntime(tmp_path) as runtime:
            assert client.request("GET", "", phase="check").data == {"ok": True}
            import socket

            with pytest.raises(RuntimeError, match="blocked"):
                socket.create_connection(("127.0.0.1", 1))
            assert len(runtime.unexpected_io) == 1
        assert len(calls) == 1


@pytest.mark.parametrize(
    "endpoint", ["file:///etc/passwd", "http://user:secret@localhost/", "http://localhost/?key=secret"]
)
def test_endpoint_credentials_and_implicit_schemes_are_rejected(endpoint):
    from story_memory_retrieval_http import BoundedHTTP

    with pytest.raises(ValueError):
        BoundedHTTP(endpoint, request_budget=1, phase_limits={"check": 1}, deadline_seconds=2)


def test_dns_deadline_cancels_and_reaps_the_resolver_process(monkeypatch):
    import asyncio

    import story_memory_retrieval_http as transport

    original = asyncio.create_subprocess_exec
    children = []

    async def tracked(*args, **kwargs):
        process = await original(*args, **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", tracked)
    monkeypatch.setattr(transport, "_DNS_PROGRAM", "import time; time.sleep(5)")
    with wire_server(lambda call: (200, {"ok": True}, {})) as (endpoint, calls):
        client = transport.BoundedHTTP(
            endpoint.replace("127.0.0.1", "localhost"),
            request_budget=1,
            phase_limits={"check": 1},
            deadline_seconds=2,
            request_timeout=0.1,
        )
        started = time.monotonic()
        with pytest.raises(transport.StudyHTTPError) as error:
            client.request("GET", "", phase="check")
        assert error.value.category == "timeout"
        assert time.monotonic() - started < 1
        assert len(children) == 1 and children[0].returncode is not None
        assert calls == []
