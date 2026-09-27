"""OpenAI Codex OAuth ownership for the native Responses transport.

The bridge keeps its own refresh-token family. Sharing copied Hermes or Codex CLI
tokens would let one process replay a rotated refresh token and revoke both sessions.
"""

from __future__ import annotations

import base64
import contextlib
import fcntl
import json
import os
import tempfile
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bridge.network_security import strict_urlopen

CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_OAUTH_ISSUER = "https://auth.openai.com"
CODEX_OAUTH_TOKEN_URL = f"{CODEX_OAUTH_ISSUER}/oauth/token"
CODEX_DEVICE_URL = f"{CODEX_OAUTH_ISSUER}/codex/device"
DEFAULT_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
_REFRESH_SKEW_SECONDS = 120
_MAX_AUTH_BODY_BYTES = 1024 * 1024

OpenRequest = Callable[..., Any]


class CodexAuthError(RuntimeError):
    """A bounded OAuth failure safe to show without exposing credentials."""

    def __init__(self, message: str, *, code: str, relogin_required: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.relogin_required = relogin_required


def _decode_jwt_claims(token: Any) -> dict[str, Any]:
    if not isinstance(token, str):
        return {}
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(payload.encode()).decode())
    except (IndexError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _token_expiring(token: str, *, now: float | None = None) -> bool:
    expires_at = _decode_jwt_claims(token).get("exp")
    if not isinstance(expires_at, (int, float)):
        return True
    current = time.time() if now is None else now
    return float(expires_at) <= current + _REFRESH_SKEW_SECONDS


@contextlib.contextmanager
def _auth_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    lock_path = path.with_name(path.name + ".lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _load_state_unlocked(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise CodexAuthError("Codex OAuth state is unreadable; log in again.", code="codex_auth_invalid") from exc
    if not isinstance(payload, dict):
        raise CodexAuthError("Codex OAuth state is invalid; log in again.", code="codex_auth_invalid")
    return payload


def _validated_tokens(state: Mapping[str, Any]) -> dict[str, str]:
    candidate = state.get("tokens")
    if not isinstance(candidate, dict):
        raise CodexAuthError(
            "No Codex OAuth login is available; run the Codex login command.",
            code="codex_auth_missing",
            relogin_required=True,
        )
    access_token = candidate.get("access_token")
    refresh_token = candidate.get("refresh_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise CodexAuthError(
            "Codex OAuth access token is missing; log in again.", code="codex_access_missing", relogin_required=True
        )
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        raise CodexAuthError(
            "Codex OAuth refresh token is missing; log in again.", code="codex_refresh_missing", relogin_required=True
        )
    return {"access_token": access_token.strip(), "refresh_token": refresh_token.strip()}


def _save_state_unlocked(path: Path, tokens: Mapping[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "auth_mode": "chatgpt",
        "last_refresh": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "tokens": {
            "access_token": str(tokens["access_token"]),
            "refresh_token": str(tokens["refresh_token"]),
        },
    }
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def save_tokens(path: Path, tokens: Mapping[str, str]) -> None:
    """Persist one independent refresh-token family with private permissions."""
    normalized = _validated_tokens({"tokens": dict(tokens)})
    with _auth_lock(path):
        _save_state_unlocked(path, normalized)


def load_tokens(path: Path) -> dict[str, str]:
    with _auth_lock(path):
        return _validated_tokens(_load_state_unlocked(path))


def auth_status(path: Path) -> dict[str, Any]:
    try:
        tokens = load_tokens(path)
    except CodexAuthError:
        return {"authenticated": False, "expires_at": None, "expiring": None}
    claims = _decode_jwt_claims(tokens["access_token"])
    expires_at = claims.get("exp") if isinstance(claims.get("exp"), (int, float)) else None
    return {
        "authenticated": True,
        "expires_at": expires_at,
        "expiring": _token_expiring(tokens["access_token"]),
    }


def logout(path: Path) -> None:
    with _auth_lock(path):
        path.unlink(missing_ok=True)


def _read_json_response(response: Any) -> dict[str, Any]:
    raw = response.read(_MAX_AUTH_BODY_BYTES + 1)
    if len(raw) > _MAX_AUTH_BODY_BYTES:
        raise CodexAuthError("OpenAI OAuth response exceeded the safety limit.", code="codex_auth_response_too_large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CodexAuthError("OpenAI OAuth returned an invalid response.", code="codex_auth_invalid_response") from exc
    if not isinstance(payload, dict):
        raise CodexAuthError("OpenAI OAuth returned an invalid response.", code="codex_auth_invalid_response")
    return payload


def _oauth_request(
    url: str,
    *,
    environ: Mapping[str, str],
    open_request: OpenRequest,
    json_body: Mapping[str, Any] | None = None,
    form_body: Mapping[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    if (json_body is None) == (form_body is None):
        raise ValueError("exactly one OAuth request body is required")
    if json_body is not None:
        data = json.dumps(dict(json_body)).encode("utf-8")
        content_type = "application/json"
    else:
        data = urllib.parse.urlencode(dict(form_body or {})).encode("utf-8")
        content_type = "application/x-www-form-urlencoded"
    request = urllib.request.Request(  # noqa: S310 - URL is fixed to the HTTPS OpenAI issuer.
        url,
        data=data,
        headers={
            "Accept": "application/json",
            "Content-Type": content_type,
            "User-Agent": "sillytavern-telegram-bridge",
        },
        method="POST",
    )
    try:
        with open_request(request, timeout=15, environ=environ) as response:
            return int(getattr(response, "status", 200)), _read_json_response(response)
    except CodexAuthError:
        raise
    except Exception as exc:
        status = getattr(exc, "code", None)
        if isinstance(status, int):
            try:
                try:
                    payload = _read_json_response(exc)
                except CodexAuthError:
                    payload = {}
            finally:
                close = getattr(exc, "close", None)
                if callable(close):
                    close()
            return status, payload
        raise CodexAuthError("OpenAI OAuth request failed.", code="codex_auth_network") from exc


def _oauth_error_code(payload: Mapping[str, Any], default: str) -> str:
    error = payload.get("error")
    if isinstance(error, Mapping):
        code = error.get("code") or error.get("type")
        return str(code or default)
    if isinstance(error, str) and error.strip():
        return error.strip()
    return default


def _refresh_tokens(
    refresh_token: str,
    *,
    environ: Mapping[str, str],
    open_request: OpenRequest,
) -> dict[str, str]:
    status, payload = _oauth_request(
        CODEX_OAUTH_TOKEN_URL,
        environ=environ,
        open_request=open_request,
        form_body={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CODEX_OAUTH_CLIENT_ID,
        },
    )
    if status == 429:
        raise CodexAuthError("OpenAI temporarily rate-limited OAuth refresh.", code="codex_rate_limited")
    if status != 200:
        code = _oauth_error_code(payload, "codex_refresh_failed")
        relogin = status in {401, 403} or code in {
            "invalid_grant",
            "invalid_token",
            "invalid_request",
            "refresh_token_reused",
        }
        raise CodexAuthError("OpenAI Codex OAuth refresh failed.", code=code, relogin_required=relogin)
    access_token = payload.get("access_token")
    rotated_refresh = payload.get("refresh_token") or refresh_token
    if not isinstance(access_token, str) or not access_token.strip():
        raise CodexAuthError("OpenAI OAuth refresh omitted the access token.", code="codex_refresh_invalid")
    if not isinstance(rotated_refresh, str) or not rotated_refresh.strip():
        raise CodexAuthError("OpenAI OAuth refresh omitted the refresh token.", code="codex_refresh_invalid")
    return {"access_token": access_token.strip(), "refresh_token": rotated_refresh.strip()}


def resolve_access_token(
    path: Path,
    *,
    environ: Mapping[str, str],
    open_request: OpenRequest = strict_urlopen,
    rejected_access_token: str | None = None,
) -> str:
    """Resolve a bearer and serialize expiry or one bounded rejection refresh."""
    with _auth_lock(path):
        tokens = _validated_tokens(_load_state_unlocked(path))
        current_access = tokens["access_token"]
        if rejected_access_token is None and not _token_expiring(current_access):
            return current_access
        if (
            rejected_access_token is not None
            and current_access != rejected_access_token
            and not _token_expiring(current_access)
        ):
            return current_access
        refreshed = _refresh_tokens(tokens["refresh_token"], environ=environ, open_request=open_request)
        _save_state_unlocked(path, refreshed)
        return refreshed["access_token"]


def device_login(
    path: Path,
    *,
    environ: Mapping[str, str],
    notify: Callable[[str, str], None],
    open_request: OpenRequest = strict_urlopen,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, str]:
    """Complete OpenAI's device-code flow and save a bridge-owned token family."""
    status, device = _oauth_request(
        f"{CODEX_OAUTH_ISSUER}/api/accounts/deviceauth/usercode",
        environ=environ,
        open_request=open_request,
        json_body={"client_id": CODEX_OAUTH_CLIENT_ID},
    )
    user_code = device.get("user_code")
    device_auth_id = device.get("device_auth_id")
    if status != 200 or not isinstance(user_code, str) or not isinstance(device_auth_id, str):
        raise CodexAuthError("OpenAI rejected the Codex device login request.", code="codex_device_start_failed")
    interval = max(3, int(device.get("interval") or 5))
    expires_in = min(60 * 60, max(1, int(device.get("expires_in") or 15 * 60)))
    notify(CODEX_DEVICE_URL, user_code)

    deadline = time.monotonic() + expires_in
    authorization: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        sleep(interval)
        poll_status, candidate = _oauth_request(
            f"{CODEX_OAUTH_ISSUER}/api/accounts/deviceauth/token",
            environ=environ,
            open_request=open_request,
            json_body={"device_auth_id": device_auth_id, "user_code": user_code},
        )
        if poll_status == 200:
            authorization = candidate
            break
        if poll_status in {429, 500, 502, 503, 504}:
            interval = min(30, interval * 2)
            continue
        if poll_status not in {403, 404}:
            raise CodexAuthError("OpenAI Codex device login failed.", code="codex_device_poll_failed")
    if authorization is None:
        raise CodexAuthError("OpenAI Codex device login expired.", code="codex_device_expired")

    authorization_code = authorization.get("authorization_code")
    code_verifier = authorization.get("code_verifier")
    if not isinstance(authorization_code, str) or not isinstance(code_verifier, str):
        raise CodexAuthError("OpenAI Codex device approval was incomplete.", code="codex_device_invalid")
    token_status, token_payload = _oauth_request(
        CODEX_OAUTH_TOKEN_URL,
        environ=environ,
        open_request=open_request,
        form_body={
            "grant_type": "authorization_code",
            "code": authorization_code,
            "redirect_uri": f"{CODEX_OAUTH_ISSUER}/deviceauth/callback",
            "client_id": CODEX_OAUTH_CLIENT_ID,
            "code_verifier": code_verifier,
        },
    )
    access_token = token_payload.get("access_token")
    refresh_token = token_payload.get("refresh_token")
    if token_status != 200 or not isinstance(access_token, str) or not isinstance(refresh_token, str):
        raise CodexAuthError("OpenAI Codex token exchange failed.", code="codex_token_exchange_failed")
    tokens = {"access_token": access_token.strip(), "refresh_token": refresh_token.strip()}
    save_tokens(path, tokens)
    return tokens


def codex_headers(access_token: str, *, client_version: str = "1.0") -> dict[str, str]:
    claims = _decode_jwt_claims(access_token)
    auth_claim = claims.get("https://api.openai.com/auth")
    auth_claim = auth_claim if isinstance(auth_claim, dict) else {}
    account_id = auth_claim.get("chatgpt_account_id")
    residency = auth_claim.get("chatgpt_data_residency") or auth_claim.get("chatgpt_compute_residency")
    headers = {
        "Accept": "text/event-stream",
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "originator": "codex_cli_rs",
        "User-Agent": f"codex_cli_rs/0.0.0 (SillyTavernTelegramBridge/{client_version})",
    }
    if isinstance(account_id, str) and account_id.strip():
        headers["ChatGPT-Account-ID"] = account_id.strip()
    if isinstance(residency, str) and residency.strip():
        headers["x-openai-internal-codex-residency"] = residency.strip()
    return headers
