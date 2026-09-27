"""Explicit installer-only Funnel provisioning; never overwrite another listener."""

from __future__ import annotations

import argparse
import http.client
import json
import re
import shutil
import subprocess
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from bridge.environment import _read_private_environment
from bridge.install_support import _atomic_file, installation_settings
from bridge.miniapp_config import MiniAppConfig, load_miniapp_config

_PORTS = (443, 8443, 10000)
_URL_KEY = "SILLYTAVERN_MINIAPP_PUBLIC_URL"
_ASSIGNMENT = re.compile(r"(?m)^[ \t]*(?:export[ \t]+)?SILLYTAVERN_MINIAPP_PUBLIC_URL[ \t]*=[^\r\n]*")


@dataclass(frozen=True)
class FunnelPlan:
    public_url: str
    public_port: int
    target: str
    reuse: bool


def _map(value: object) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("Unrecognized Tailscale status. Update Tailscale and inspect its Serve configuration.")
    return value


def _validate_state(state: dict, depth: int = 0) -> None:
    if depth > 4 or not set(state) <= {"TCP", "Web", "AllowFunnel", "Foreground", "Services", "Tun"}:
        raise ValueError("Unrecognized Tailscale Serve configuration; inspect status before retrying.")
    for name in ("TCP", "Web", "AllowFunnel", "Foreground", "Services"):
        _map(state.get(name))
    for name in ("Foreground", "Services"):
        for child in _map(state.get(name)).values():
            _validate_state(_map(child), depth + 1)


def _on_port(mapping: dict, port: int) -> dict:
    return {key: value for key, value in mapping.items() if str(key).rsplit(":", 1)[-1] == str(port)}


def _port_used(state: dict, port: int) -> bool:
    if any(_on_port(_map(state.get(name)), port) for name in ("TCP", "Web", "AllowFunnel")):
        return True
    return any(
        _port_used(_map(child), port) for name in ("Foreground", "Services") for child in _map(state.get(name)).values()
    )


def _exact_proxy(state: dict, host: str, port: int, target: str) -> bool:
    key = f"{host}:{port}"
    return (
        _on_port(_map(state.get("TCP")), port) == {str(port): {"HTTPS": True}}
        and _on_port(_map(state.get("Web")), port) == {key: {"Handlers": {"/": {"Proxy": target}}}}
        and _on_port(_map(state.get("AllowFunnel")), port) == {key: True}
        and not any(
            _port_used(_map(child), port)
            for name in ("Foreground", "Services")
            for child in _map(state.get(name)).values()
        )
    )


def plan_funnel(config: MiniAppConfig, status: dict, serve: dict) -> FunnelPlan:
    """Choose one unoccupied public port, or reuse the exact already-public proxy."""
    status, serve = _map(status), _map(serve)
    _validate_state(serve)
    if status.get("BackendState") != "Running":
        raise ValueError("Tailscale must be installed, running and authenticated before Funnel setup.")
    host = str(_map(status.get("Self")).get("DNSName") or "").lower().rstrip(".")
    labels = host.split(".")
    if (
        len(host) > 253
        or len(labels) < 4
        or labels[-2:] != ["ts", "net"]
        or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)
    ):
        raise ValueError("Tailscale did not supply a valid node DNS name; check MagicDNS.")
    if config.host != "127.0.0.1" or not 1024 <= config.port <= 65535:
        raise ValueError("Funnel must forward only to the configured loopback Mini App port.")
    target = f"http://127.0.0.1:{config.port}"
    ports: tuple[int, ...] = _PORTS
    if config.public_url:
        parts = urlsplit(config.public_url)
        if (
            parts.scheme != "https"
            or parts.hostname != host
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
            or parts.path != "/miniapp/"
            or (parts.port or 443) not in _PORTS
        ):
            raise ValueError("Mini App URL must match this Tailscale node and port 443, 8443 or 10000.")
        ports = (parts.port or 443,)
    for port in ports:
        if _exact_proxy(serve, host, port, target):
            suffix = "" if port == 443 else f":{port}"
            return FunnelPlan(f"https://{host}{suffix}/miniapp/", port, target, True)
    for port in ports:
        if not _port_used(serve, port):
            suffix = "" if port == 443 else f":{port}"
            return FunnelPlan(f"https://{host}{suffix}/miniapp/", port, target, False)
    raise ValueError("Requested Funnel ports are already in use. Existing private/public services were preserved.")


def _tailscale(*args: str, interactive: bool = False) -> str:
    executable = shutil.which("tailscale")
    if not executable:
        raise ValueError("Install Tailscale 1.52+ and authenticate this device before using --with-tailscale-funnel.")
    try:
        result = subprocess.run(  # noqa: S603 -- fixed CLI operations, validated loopback target and numeric port
            [executable, *args],
            capture_output=not interactive,
            text=True,
            check=True,
            timeout=120 if interactive else 15,
        )
    except (OSError, subprocess.SubprocessError):
        raise ValueError(
            "Tailscale command failed. Check login, operator permissions and HTTPS/Funnel authorization; "
            "inspect 'tailscale funnel status' before retrying."
        ) from None
    return result.stdout or ""


def _json_status(raw: str) -> dict:
    if len(raw) > 1024 * 1024:
        raise ValueError("Tailscale status exceeds its size limit.")
    return _map(json.loads(raw))


def _discover(config: MiniAppConfig) -> FunnelPlan:
    version = _tailscale("version").splitlines()[0]
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version)
    if match is None or tuple(map(int, match.groups())) < (1, 52, 0):
        raise ValueError("Tailscale 1.52 or newer is required for this installer.")
    return plan_funnel(
        config,
        _json_status(_tailscale("status", "--json")),
        _json_status(_tailscale("funnel", "status", "--json")),
    )


def store_public_url(env_path: Path, public_url: str) -> None:
    """Fill one blank assignment without evaluating or rewriting any other setting."""
    original = _read_private_environment(env_path)
    if original is None:
        raise ValueError("Prepare the private .env before configuring Funnel.")
    assignments = list(_ASSIGNMENT.finditer(original))
    if len(assignments) > 1:
        raise ValueError("Remove duplicate Mini App URL assignments before configuring Funnel.")
    if assignments:
        match = assignments[0]
        value = match.group().split("=", 1)[1].strip()
        if value in {public_url, f'"{public_url}"', f"'{public_url}'"}:
            return
        if value not in {"", '""', "''"}:
            raise ValueError("The configured Mini App URL was preserved; clear it explicitly to select a new URL.")
        updated = original[: match.start()] + f"{_URL_KEY}={public_url}" + original[match.end() :]
    else:
        updated = original + ("" if original.endswith("\n") else "\n") + f"{_URL_KEY}={public_url}\n"
    if _read_private_environment(env_path) != original:
        raise ValueError("Environment changed while preparing Funnel; retry without concurrent edits.")
    _atomic_file(env_path, updated.encode("utf-8"))


def prepare_funnel(source: Path, home: Path, env_path: Path) -> FunnelPlan:
    config = load_miniapp_config(installation_settings(source, home, env_path))
    plan = _discover(config)
    if not config.public_url:
        store_public_url(env_path, plan.public_url)
    return plan


def verify_backend(config: MiniAppConfig) -> None:
    """Require this release's shell and an unauthenticated API rejection before publishing."""
    expected = (Path(__file__).with_name("miniapp_assets") / "index.html").read_bytes()
    headers = {"Host": urlsplit(config.public_url).netloc, "Origin": config.origin}
    for _attempt in range(10):
        try:
            with closing(http.client.HTTPConnection("127.0.0.1", config.port, timeout=2)) as connection:
                connection.request("GET", "/miniapp/", headers=headers)
                response = connection.getresponse()
                if response.status != 200 or response.read(65537) != expected:
                    raise ValueError("Loopback server is not the expected Mini App; Funnel was not enabled.")
                connection.request("GET", "/api/v1/me", headers=headers)
                response = connection.getresponse()
                body = response.read(65537)
                if response.status != 401 or json.loads(body).get("error", {}).get("code") != "auth":
                    raise ValueError("Mini App authentication check failed; Funnel was not enabled.")
                return
        except (OSError, http.client.HTTPException):
            time.sleep(0.5)
    raise ValueError("Mini App did not become ready on loopback; Funnel was not enabled.")


def enable_funnel(source: Path, home: Path, env_path: Path) -> FunnelPlan:
    config = load_miniapp_config(installation_settings(source, home, env_path))
    if not config.enabled:
        raise ValueError("Prepare the Mini App public URL before enabling Funnel.")
    plan = _discover(config)
    verify_backend(config)
    # Recheck after startup/probe latency; never knowingly replace a concurrent listener.
    if _discover(config) != plan:
        raise ValueError("Tailscale configuration changed during setup; no listener was replaced.")
    if not plan.reuse:
        _tailscale("funnel", "--bg", f"--https={plan.public_port}", plan.target, interactive=True)
    confirmed = _discover(config)
    if not confirmed.reuse:
        raise ValueError("Funnel did not confirm the expected public proxy. Inspect its status before retrying.")
    return confirmed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "enable"))
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--env", type=Path, required=True)
    args = parser.parse_args()
    try:
        action = prepare_funnel if args.action == "prepare" else enable_funnel
        plan = action(args.source, args.home, args.env)
        label = "Prepared URL (no publication changes)" if args.action == "prepare" else "Funnel proxy verified"
        print(f"{label}: {plan.public_url}")
        return 0
    except ValueError as exc:
        print(str(exc))
        return 1
    except Exception:
        print("Funnel setup failed. Check private configuration and Tailscale status; no reset was attempted.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
