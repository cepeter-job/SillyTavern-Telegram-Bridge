"""In-process test socket guard; not an operating-system or subprocess sandbox."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit


def is_local_destination(family: int, address: object) -> bool:
    if family == getattr(socket, "AF_UNIX", None):
        return True
    if family not in {socket.AF_INET, socket.AF_INET6}:
        return False
    if not isinstance(address, tuple) or not address or not isinstance(address[0], str):
        return False
    try:
        numeric = ipaddress.ip_address(address[0])
    except ValueError:
        return False
    if isinstance(numeric, ipaddress.IPv6Address) and numeric.ipv4_mapped is not None:
        numeric = numeric.ipv4_mapped
    return numeric.is_loopback


def guard_connect(original: Callable[[Any, Any], Any], attempts: list[str]) -> Callable[[Any, Any], Any]:
    def connect(sock: Any, address: Any) -> Any:
        if not is_local_destination(sock.family, address):
            attempts.append("non-loopback socket attempt")
            raise AssertionError("Test attempted a non-loopback connection; mock the external transport")
        return original(sock, address)

    return connect


def _check_http_host(host: str | None, attempts: list[str]) -> None:
    # A local proxy's socket is not proof that the requested HTTP destination is local.
    if host == "localhost" or is_local_destination(socket.AF_INET, (host, 0)):
        return
    attempts.append("external HTTP request")
    raise AssertionError("Test attempted external HTTP; mock the external transport before request construction")


def guard_http_request(original: Callable[..., Any], attempts: list[str]) -> Callable[..., Any]:
    def putrequest(connection: Any, method: str, url: str, *args: Any, **kwargs: Any) -> Any:
        host = urlsplit(url).hostname or getattr(connection, "_tunnel_host", None) or connection.host
        _check_http_host(host, attempts)
        return original(connection, method, url, *args, **kwargs)

    return putrequest


def guard_httpx_send(original: Callable[..., Any], attempts: list[str]) -> Callable[..., Any]:
    def send(client: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
        _check_http_host(urlsplit(str(request.url)).hostname, attempts)
        return original(client, request, *args, **kwargs)

    return send
