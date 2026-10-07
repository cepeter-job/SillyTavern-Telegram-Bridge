"""Single-dispatch HTTP with absolute deadlines, owned sockets and sanitized failures."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import sys
import time
from contextlib import ExitStack
from dataclasses import dataclass
from unittest.mock import patch
from urllib.parse import urlsplit

import aiohttp
from story_memory_retrieval_fixture import require, sha256

from bridge.network_security import EndpointPolicy, EndpointPolicyError

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_DNS_PROGRAM = (
    "import json,socket,sys; "
    "rows=socket.getaddrinfo(sys.argv[1],int(sys.argv[2]),type=socket.SOCK_STREAM); "
    "sys.stdout.write(json.dumps(rows if len(rows)<=32 else []))"
)


class StudyHTTPError(RuntimeError):
    def __init__(self, category: str, *, attempted: bool = False, status: int | None = None):
        super().__init__(category)
        self.category, self.attempted, self.status = category, attempted, status


@dataclass(frozen=True)
class HTTPResult:
    status: int
    data: dict | None
    elapsed_ns: int


class _PinnedResolver(aiohttp.abc.AbstractResolver):
    """A cancellable DNS subprocess avoids unbounded default-executor shutdown."""

    def __init__(self, policy: EndpointPolicy):
        self.policy = policy

    async def resolve(self, host, port=0, family=socket.AF_INET):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-c",
            _DNS_PROGRAM,
            host,
            str(port),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={},
        )
        try:
            output, _ = await process.communicate()
            require(process.returncode == 0 and len(output) <= 65536, "DNS resolution unavailable")
            rows = json.loads(output)
            require(0 < len(rows) <= 32, "Invalid DNS result count")
            for row in rows:
                require(row[0] in {socket.AF_INET, socket.AF_INET6}, "Unsupported DNS address family")
                self.policy.validate_address(host, ipaddress.ip_address(row[4][0]))
            # One chosen socket destination; no implicit address fallback/retry.
            selected = next((row for row in rows if family in {0, row[0]}), rows[0])
            return [
                {
                    "hostname": host,
                    "host": selected[4][0],
                    "port": port,
                    "family": selected[0],
                    "proto": selected[2],
                    "flags": socket.AI_NUMERICHOST,
                }
            ]
        finally:
            if process.returncode is None:
                process.kill()
            await process.wait()

    async def close(self):
        return None


class BoundedHTTP:
    """Construct before the offline corpus so only this explicit HTTP seam may connect."""

    def __init__(
        self,
        endpoint,
        *,
        request_budget,
        phase_limits,
        deadline_seconds,
        request_timeout=10,
        token="",
        cleanup_reserve=0,
        cleanup_reserve_seconds=0,
    ):
        require(isinstance(endpoint, str) and len(endpoint) <= 2048, "Invalid explicit endpoint")
        try:
            parsed = urlsplit(endpoint)
            require(
                not parsed.query and not parsed.fragment and parsed.username is None and parsed.password is None,
                "Endpoint cannot contain URL credentials, query or fragment",
            )
            self.policy = EndpointPolicy(allowed_hosts=frozenset({parsed.hostname or ""}))
            scheme, host, port = self.policy.validate(endpoint)
        except (EndpointPolicyError, ValueError) as exc:
            raise ValueError("Invalid explicit study endpoint") from exc
        require(type(request_budget) is int and 1 <= request_budget <= 38, "Invalid request budget")
        require(0 < request_timeout <= 10 and 0 < deadline_seconds <= 300, "Invalid absolute request/run deadline")
        require(all(type(value) is int and 0 <= value <= 38 for value in phase_limits.values()), "Invalid phase cap")
        require(
            0 <= cleanup_reserve <= 3 and 0 <= cleanup_reserve_seconds < deadline_seconds, "Invalid cleanup reserve"
        )
        require(
            isinstance(token, str) and not any(ord(char) < 32 or ord(char) == 127 for char in token),
            "Invalid credential",
        )
        self.endpoint = endpoint.rstrip("/")
        self.identity = {
            "endpoint_url": self.endpoint,
            "endpoint_sha256": sha256(self.endpoint),
            "scheme": scheme,
            "host": host,
            "port": port,
            "inference_boundary": "external_provider",
            "transport": "aiohttp",
            "transport_version": aiohttp.__version__,
        }
        self._token = token
        self.request_budget, self.phase_limits = request_budget, dict(phase_limits)
        self.deadline_seconds, self.request_timeout = deadline_seconds, request_timeout
        self.cleanup_reserve, self.cleanup_reserve_seconds = cleanup_reserve, cleanup_reserve_seconds
        self.count = 0
        self.phase_counts = dict.fromkeys(phase_limits, 0)
        self.deadline = None
        self.observations = []
        self._socket_methods = [
            (owner, name, getattr(owner, name))
            for owner, name in (
                (socket.socket, "connect"),
                (socket.socket, "connect_ex"),
                (socket, "create_connection"),
                (socket, "getaddrinfo"),
                (socket.socket, "sendto"),
            )
        ]

    def begin(self):
        if self.deadline is None:
            self.deadline = time.monotonic() + self.deadline_seconds

    def request(self, method, path, payload=None, *, phase, acceptable=(200,)) -> HTTPResult:
        self.begin()
        require(method in {"GET", "POST", "PUT", "DELETE"}, "Unsupported study HTTP method")
        require(path == "" or (path.startswith("/") and not any(c in path for c in "?#\\")), "Invalid request path")
        reserve = 0 if phase == "cleanup" else self.cleanup_reserve
        if (
            phase not in self.phase_limits
            or self.count >= self.request_budget - reserve
            or self.phase_counts[phase] >= self.phase_limits[phase]
        ):
            raise StudyHTTPError("request_cap")
        remaining = self.deadline - time.monotonic() - (0 if phase == "cleanup" else self.cleanup_reserve_seconds)
        if remaining <= 0:
            raise StudyHTTPError("deadline")
        body = None if payload is None else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        require(body is None or len(body) <= 1024 * 1024, "Request body cap exceeded")
        self.count += 1
        self.phase_counts[phase] += 1
        started = time.perf_counter_ns()
        absolute_deadline = time.monotonic() + min(self.request_timeout, remaining)
        observation = {"phase": phase, "method": method, "status": None, "category": "", "elapsed_ns": 0}
        try:
            # Native/provider guards remain installed; this scope owns only one bounded HTTP attempt.
            with ExitStack() as sockets:
                for owner, name, original in self._socket_methods:
                    sockets.enter_context(patch.object(owner, name, original))
                status, data = asyncio.run(self._request(method, path, body, acceptable, absolute_deadline))
            observation["status"] = status
            return HTTPResult(status, data, time.perf_counter_ns() - started)
        except StudyHTTPError as exc:
            observation.update(category=exc.category, status=exc.status)
            raise
        except (TimeoutError, asyncio.TimeoutError) as exc:
            observation["category"] = "timeout"
            raise StudyHTTPError("timeout", attempted=True) from exc
        except Exception as exc:
            observation["category"] = "transport_error"
            raise StudyHTTPError("transport_error", attempted=True) from exc
        finally:
            observation["elapsed_ns"] = time.perf_counter_ns() - started
            self.observations.append(observation)

    async def _request(self, method, path, body, acceptable, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise StudyHTTPError("deadline", attempted=False)
        headers = {"Content-Type": "application/json", "Accept-Encoding": "identity"}
        if self._token:
            headers["Authorization"] = "Bearer " + self._token
        async with asyncio.timeout(remaining):
            resolver = _PinnedResolver(self.policy)
            connector = aiohttp.TCPConnector(
                resolver=resolver, use_dns_cache=False, force_close=True, limit=1, happy_eyeballs_delay=None
            )
            async with aiohttp.ClientSession(
                connector=connector,
                trust_env=False,
                cookie_jar=aiohttp.DummyCookieJar(),
                auto_decompress=False,
                timeout=aiohttp.ClientTimeout(total=remaining, ceil_threshold=300),
            ) as session:
                # Verified in the locked aiohttp transport; fail closed on an incompatible runtime.
                require(hasattr(session, "_retry_connection"), "Transport retry control unavailable")
                session._retry_connection = False
                async with session.request(
                    method, self.endpoint + path, data=body, headers=headers, allow_redirects=False, proxy=None
                ) as response:
                    if response.status not in acceptable:
                        raise StudyHTTPError("http_status", attempted=True, status=response.status)
                    if response.status == 404 or method == "DELETE":
                        return response.status, None
                    if response.content_length is not None and response.content_length > MAX_RESPONSE_BYTES:
                        raise StudyHTTPError("response_cap", attempted=True, status=response.status)
                    content = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        content.extend(chunk)
                        if len(content) > MAX_RESPONSE_BYTES:
                            raise StudyHTTPError("response_cap", attempted=True, status=response.status)
                    try:
                        data = json.loads(content) if content else None
                    except (ValueError, UnicodeError) as exc:
                        raise StudyHTTPError("invalid_json", attempted=True, status=response.status) from exc
                    if data is not None and not isinstance(data, dict):
                        raise StudyHTTPError("invalid_json", attempted=True, status=response.status)
                    return response.status, data
