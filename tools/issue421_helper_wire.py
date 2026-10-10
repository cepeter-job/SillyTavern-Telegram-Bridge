"""New, finite physical-call ledger; no change to the frozen native-v1 protocol."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
from pathlib import Path

ENDPOINT = "https://nano-gpt.com/api/subscription/v1/chat/completions"
MAX_REQUESTS, MAX_INPUT, MAX_OUTPUT, MAX_BODY = 24, 300000, 32000, 150000
MAX_RESPONSE = 200000


def atomic_json(path: Path, value: object) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".trial-", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


class StoredResponse(io.BytesIO):
    status = 200

    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {"Content-Type": "application/json"}

    def getcode(self):
        return self.status


class TrialWire:
    """Own a single non-resumable study: pending or unknown delivery halts it."""

    def __init__(
        self,
        path: Path,
        *,
        quota,
        opener,
        maximum_requests=MAX_REQUESTS,
        maximum_input=MAX_INPUT,
        maximum_output=MAX_OUTPUT,
    ) -> None:
        limits = ((maximum_requests, MAX_REQUESTS), (maximum_input, MAX_INPUT), (maximum_output, MAX_OUTPUT))
        if any(type(value) is not int or not 1 <= value <= ceiling for value, ceiling in limits):
            raise ValueError("trial_limit_invalid")
        self.maximum_requests = maximum_requests
        self.maximum_input = maximum_input
        self.maximum_output = maximum_output
        if path.exists() or path.is_symlink():
            raise ValueError("trial_ledger_exists")
        self.path, self.quota, self.opener = path, quota, opener
        self.state = {"schema_version": 1, "attempts": [], "stopped": False, "logical_cases": []}
        self.context = None
        atomic_json(path, self.state)

    def set_context(self, case_id: str, variant: str) -> None:
        if variant not in {"baseline", "candidate"} or not case_id or len(case_id) > 100:
            raise ValueError("invalid_trial_context")
        self.context = (case_id, variant)

    def __call__(self, request, **kwargs):
        if self.state["stopped"]:
            raise ValueError("trial_stopped")
        if request.full_url != ENDPOINT or request.get_method() != "POST":
            raise ValueError("trial_route_refused")
        encoded = request.data
        if not isinstance(encoded, bytes) or not 1 <= len(encoded) <= MAX_BODY:
            raise ValueError("trial_body_refused")
        body = json.loads(encoded)
        cap = body.get("max_tokens")
        if (
            body.get("model") != "z-ai/glm-5.2"
            or body.get("stream") is not False
            or type(cap) is not int
            or not 1 <= cap <= 1400
            or not self.context
        ):
            raise ValueError("trial_body_refused")
        attempts = self.state["attempts"]
        known_input = sum(row["usage"]["input_tokens"] for row in attempts if row["usage"] is not None)
        output_reserved = sum(row["output_cap"] for row in attempts)
        if (
            len(attempts) >= self.maximum_requests
            or known_input + len(encoded) + 512 > self.maximum_input
            or output_reserved + cap > self.maximum_output
        ):
            self.state["stopped"] = True
            atomic_json(self.path, self.state)
            raise ValueError("trial_budget_exhausted")
        # Existing subscription admission checks require active membership,
        # allowOverage=false and a 100,000-unit reserve. Never log account data.
        try:
            self.quota(len(encoded))
        except Exception:
            self.state["stopped"] = True
            atomic_json(self.path, self.state)
            raise
        row = {
            "case_id": self.context[0],
            "variant": self.context[1],
            "ordinal": len(attempts) + 1,
            "payload": body,
            "payload_sha256": hashlib.sha256(encoded).hexdigest(),
            "input_reserve": len(encoded) + 512,
            "output_cap": cap,
            "status": "pending",
            "usage": None,
        }
        attempts.append(row)
        atomic_json(self.path, self.state)
        try:
            with self.opener(request, **kwargs) as response:
                raw = response.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                raise ValueError("trial_response_size")
            obj = json.loads(raw)
            usage = obj.get("usage") if isinstance(obj, dict) else None
            if not isinstance(usage, dict):
                raise ValueError("trial_usage_unknown")
            inputs, outputs = usage.get("prompt_tokens"), usage.get("completion_tokens")
            details = usage.get("prompt_tokens_details", {})
            cached = details.get("cached_tokens") if isinstance(details, dict) else None
            if (
                type(inputs) is not int
                or not 0 < inputs <= row["input_reserve"]
                or type(outputs) is not int
                or not 0 <= outputs <= cap
                or (cached is not None and (type(cached) is not int or not 0 <= cached <= inputs))
            ):
                raise ValueError("trial_usage_out_of_bounds")
            row["usage"] = {"input_tokens": inputs, "cached_tokens": cached, "output_tokens": outputs}
            row["response"] = obj
            row["status"] = "complete"
            atomic_json(self.path, self.state)
            return StoredResponse(raw)
        except BaseException as error:
            row["status"] = "failed"
            row["error_type"] = type(error).__name__
            self.state["stopped"] = True
            atomic_json(self.path, self.state)
            raise
