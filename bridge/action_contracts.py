"""Bounded, evidence-backed adjudication proposals; models never supply random outcomes."""

from __future__ import annotations

import json
import re
from typing import Any

from bridge.json_fences import unfence_json


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate adjudication field")
        result[key] = value
    return result


def _constant(value: str) -> Any:
    raise ValueError("Non-finite adjudication value")


def _text(value: Any, maximum: int = 500) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.encode()) > maximum:
        raise ValueError("Adjudication text must be nonempty and bounded")
    return value.strip()


def parse_action_proposal(raw: str, evidence: dict[str, str]) -> dict[str, Any]:
    if not isinstance(raw, str) or len(raw.encode()) > 12000:
        raise ValueError("Adjudication response exceeds its bound")
    try:
        data = json.loads(unfence_json(raw), object_pairs_hook=_object, parse_constant=_constant)
    except (TypeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("Adjudication requires a JSON object") from exc
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("Unsupported adjudication schema")
    decision = data.get("decision")
    if not isinstance(decision, str) or decision not in {"no_check", "auto_success", "check"}:
        raise ValueError("Unknown adjudication decision")
    allowed = {"schema_version", "decision", "reason"}
    if decision == "check":
        allowed |= {"domain", "dc", "success", "failure", "evidence"}
    if data.keys() - allowed:
        raise ValueError("Models cannot supply dice, modifiers or mechanical outcomes")
    result: dict[str, Any] = {"decision": decision}
    if "reason" in data:
        result["reason"] = _text(data["reason"])
    if decision != "check":
        return result
    domain = _text(data.get("domain"), 40).casefold()
    if not re.fullmatch(r"[a-z][a-z0-9_/-]{0,39}", domain):
        raise ValueError("Invalid skill domain")
    if type(data.get("dc")) is not int or not 1 <= data["dc"] <= 20:
        raise ValueError("Difficulty must be an integer from 1 to 20")
    proof = data.get("evidence")
    if not isinstance(proof, list) or not 1 <= len(proof) <= 4:
        raise ValueError("Checks require committed evidence")
    references = []
    for item in proof:
        if not isinstance(item, dict) or set(item) != {"source", "quote"}:
            raise ValueError("Invalid adjudication evidence reference")
        source, quote = _text(item["source"], 80), _text(item["quote"])
        if source not in evidence or len(quote) < 8 or quote not in evidence[source]:
            raise ValueError("Adjudication evidence is not present in the captured source")
        references.append({"source": source, "quote": quote})
    result.update(
        domain=domain,
        dc=data["dc"],
        reason=_text(data.get("reason")),
        success=_text(data.get("success")),
        failure=_text(data.get("failure")),
        evidence=references,
    )
    return result
