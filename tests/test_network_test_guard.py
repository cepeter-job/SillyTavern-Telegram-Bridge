"""Test-network safeguards are exercised without contacting external services."""

from __future__ import annotations

import importlib
import socket
from types import SimpleNamespace

import pytest


def helper():
    return importlib.import_module("network_test_support")


@pytest.mark.parametrize(
    "family,address,allowed",
    [
        (socket.AF_INET, ("127.0.0.1", 80), True),
        (socket.AF_INET, ("127.10.20.30", 80), True),
        (socket.AF_INET6, ("::1", 80, 0, 0), True),
        (socket.AF_INET6, ("::ffff:127.0.0.1", 80, 0, 0), True),
        (socket.AF_INET, ("1.1.1.1", 80), False),
        (socket.AF_INET, ("192.168.1.1", 80), False),
        (socket.AF_INET, ("100.64.0.1", 80), False),
        (socket.AF_INET, ("0.0.0.0", 80), False),  # noqa: S104 -- rejected-address fixture; no socket is bound
        (socket.AF_INET6, ("::", 80, 0, 0), False),
        (socket.AF_INET, ("localhost", 80), False),
        (socket.AF_INET, ("untrusted.example", 80), False),
    ],
)
def test_only_numeric_loopback_internet_destinations_are_allowed(family, address, allowed):
    assert helper().is_local_destination(family, address) is allowed


def test_local_unix_socket_is_allowed():
    if not hasattr(socket, "AF_UNIX"):
        pytest.skip("Unix-domain sockets not available")
    assert helper().is_local_destination(socket.AF_UNIX, "/tmp/test-only.sock")


def test_rejected_attempt_is_recorded_before_any_connection():
    attempts, actual = [], []

    def original(sock, address):
        actual.append(address)

    guarded = helper().guard_connect(original, attempts)
    sock = SimpleNamespace(family=socket.AF_INET)
    with pytest.raises(AssertionError, match="non-loopback"):
        guarded(sock, ("1.1.1.1", 443))
    assert actual == []
    assert len(attempts) == 1


def test_allowed_loopback_connection_forwards_exact_socket_address_and_return():
    calls, attempts = [], []
    sock = SimpleNamespace(family=socket.AF_INET)
    address = ("127.0.0.1", 12345)

    def original(current, target):
        calls.append((current, target))
        return 17

    guarded = helper().guard_connect(original, attempts)
    assert guarded(sock, address) == 17
    assert calls == [(sock, address)]
    assert attempts == []


@pytest.mark.parametrize(
    "tunnel,url",
    [("api.telegram.org", "/bot-fixture/sendMessage"), (None, "https://api.telegram.org/bot-fixture/sendMessage")],
)
def test_loopback_proxy_cannot_forward_an_external_http_request(tunnel, url):
    attempts, calls = [], []
    connection = SimpleNamespace(host="127.0.0.1", _tunnel_host=tunnel)
    guarded = helper().guard_http_request(lambda *args, **kwargs: calls.append(args), attempts)
    with pytest.raises(AssertionError, match="external HTTP"):
        guarded(connection, "POST", url)
    assert not calls
    assert attempts == ["external HTTP request"]


def test_http_guard_allows_explicit_loopback_test_server():
    attempts, calls = [], []
    connection = SimpleNamespace(host="::1", _tunnel_host=None)
    guarded = helper().guard_http_request(lambda *args, **kwargs: calls.append(args) or 7, attempts)
    assert guarded(connection, "GET", "/fixture", skip_host=True) == 7
    assert calls == [(connection, "GET", "/fixture")]
    assert not attempts


def test_httpx_guard_rejects_external_url_before_send():
    attempts, calls = [], []
    guarded = helper().guard_httpx_send(lambda *args, **kwargs: calls.append(args), attempts)
    with pytest.raises(AssertionError, match="external HTTP"):
        guarded(object(), SimpleNamespace(url="https://api.telegram.org/bot-fixture/sendMessage"))
    assert not calls and attempts == ["external HTTP request"]
