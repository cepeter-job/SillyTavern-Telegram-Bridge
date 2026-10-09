"""One physical request per admission, strictly on NanoGPT's subscription route."""

from __future__ import annotations

import hashlib
import json
import math
import urllib.request
from collections.abc import Callable, Mapping

from bridge.network_security import strict_urlopen
from tools.native_context_limits import TrialBudget, verify_subscription

BASE = "https://nano-gpt.com/api/subscription/v1"
MAX_RESPONSE_BYTES = 200000


def _decode(data: bytes) -> dict:
    if len(data) > MAX_RESPONSE_BYTES:
        raise ValueError("native_response_too_large")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("native_response_duplicate_key")
            result[key] = value
        return result

    result = json.loads(data, object_pairs_hook=unique)
    if not isinstance(result, dict):
        raise ValueError("native_response_not_object")
    return result


class SubscriptionClient:
    def __init__(
        self,
        model: str,
        key: str,
        environ: Mapping[str, str],
        budget: TrialBudget,
        *,
        opener: Callable = strict_urlopen,
    ) -> None:
        self.model = model
        self._key = key
        self._environ = environ
        self.budget = budget
        self._open = opener
        self._pending_digest: str | None = None
        self.last_usage: dict | None = None
        self.last_quota: int | None = None
        self.stopped = False

    def quota(self, request_bytes: int) -> int:
        request = urllib.request.Request(  # noqa: S310 -- fixed subscription host through DNS-pinned opener
            BASE + "/usage", headers={"x-api-key": self._key, "Accept": "application/json"}, method="GET"
        )
        with self._open(request, timeout=20, environ=self._environ) as response:
            data = _decode(response.read(MAX_RESPONSE_BYTES + 1))
        self.last_quota = verify_subscription(data, minimum_reserve=100000, request_bytes=request_bytes)
        return self.last_quota

    def admit(self, body: dict) -> bytes:
        """Verify quota and reserve before the caller persists a pending attempt."""
        if self.stopped or self.budget.unknown_usage or self.budget.pending_input_reserve:
            raise ValueError("native_transport_stopped_or_pending")
        required = {"model", "messages", "temperature", "max_tokens", "stream"}
        if (
            not isinstance(body, dict)
            or not required.issubset(body)
            or set(body) - required - {"response_format"}
            or body["model"] != self.model
            or body["stream"] is not False
            or type(body["temperature"]) not in (int, float)
            or not math.isfinite(body["temperature"])
            or not 0 <= body["temperature"] <= 2
            or not isinstance(body["messages"], list)
            or not 1 <= len(body["messages"]) <= 290
            or any(
                not isinstance(msg, dict)
                or set(msg) != {"role", "content"}
                or msg["role"] not in {"system", "user", "assistant"}
                or not isinstance(msg["content"], str)
                for msg in body["messages"]
            )
            or ("response_format" in body and body["response_format"] != {"type": "json_object"})
        ):
            raise ValueError("native_request_shape_or_route_invalid")
        encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(encoded) > self.budget.maximum_request_bytes:
            raise ValueError("native_request_body_too_large")
        self.quota(len(encoded))
        self.budget.reserve(len(encoded), body["max_tokens"])
        self._pending_digest = hashlib.sha256(encoded).hexdigest()
        self.last_usage = None
        return encoded

    def send_reserved(self, encoded: bytes) -> dict:
        """No retry, continuation, repair, paid fallback or provider-pin path."""
        if self.stopped or self._pending_digest is None or hashlib.sha256(encoded).hexdigest() != self._pending_digest:
            raise ValueError("native_unreserved_or_changed_request")
        # Clear dispatch capability before calling the socket. An ambiguous
        # delivery failure must never be replayed automatically.
        self._pending_digest = None
        request = urllib.request.Request(  # noqa: S310 -- fixed subscription host through DNS-pinned opener
            BASE + "/chat/completions",
            data=encoded,
            method="POST",
            headers={
                "Authorization": "Bearer " + self._key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with self._open(request, timeout=180, environ=self._environ) as response:
                result = _decode(response.read(MAX_RESPONSE_BYTES + 1))
            # Account before validating prose, so malformed/truncated output
            # cannot disappear from the total input denominator.
            self.last_usage = self.budget.complete(result.get("usage"))
            choices = result.get("choices")
            if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
                raise ValueError("native_response_choices_invalid")
            choice = choices[0]
            message = choice.get("message")
            text = message.get("content") if isinstance(message, dict) else None
            if (
                choice.get("finish_reason") != "stop"
                or not isinstance(text, str)
                or not 1 <= len(text.strip()) <= 16000
            ):
                raise ValueError("native_response_empty_or_truncated")
            observed_model = result.get("model")
            if observed_model is not None and (not isinstance(observed_model, str) or len(observed_model) > 200):
                raise ValueError("native_response_model_invalid")
            return {"output": text, "usage": self.last_usage, "response_model": observed_model, "finish_reason": "stop"}
        except Exception:
            self.stopped = True
            if self.budget.pending_input_reserve:
                self.budget.unknown_usage = True
            raise


def configured_client(selection: str, budget: TrialBudget) -> SubscriptionClient:
    """Resolve credentials without printing or copying them into trial artifacts."""
    import os
    from pathlib import Path

    from bridge.environment import bootstrap_environment
    from bridge.model_router import ModelRouter
    from bridge.provider_catalog import load_routing_catalog
    from bridge.provider_transport import _resolve_provider_credential
    from bridge.settings import load_app_settings

    environ = dict(os.environ)
    bootstrap_environment(environ)
    settings = load_app_settings(environ, home=Path.home())
    route = ModelRouter(lambda: load_routing_catalog(app_settings=settings)).route(selection)
    endpoint = str(route.spec.get("api_endpoint") or route.spec.get("api") or "").rstrip("/")
    if route.provider_id != "nano-gpt" or endpoint != BASE:
        raise ValueError("native_trial_requires_subscription_route")
    key = _resolve_provider_credential(dict(route.spec), "", "LLM_API_KEY", "NanoGPT", app_settings=settings)
    return SubscriptionClient(route.model_id, key, settings.environ, budget)
