"""Fail-closed parsing of external supporting-character extraction results."""

from __future__ import annotations

import json

import bridge.limits as _limits
from bridge.extraction_contracts import record_tracker_contract
from bridge.json_fences import unfence_json
from bridge.npc_service import _expected_mode, normalize_npc_name, validate_npc_operation
from bridge.npc_types import NpcExtractionGroup, NpcOperation


def _audience_rejection_code(raw_op: dict, field: str, visibility: str, known_by: object) -> str:
    """Keep the original validation order; return only the first fixed audience rule."""
    if "visibility" in raw_op and not isinstance(raw_op["visibility"], str):
        return "visibility_type_invalid"
    if visibility not in {"shared", "restricted"}:
        return "visibility_value_invalid"
    if not isinstance(known_by, list):
        return "known_by_type_invalid"
    if len(known_by) > _limits.NPC_MAX_ALIASES:
        return "known_by_limit_exceeded"
    if any(not isinstance(knower, str) or not knower.strip() for knower in known_by):
        return "known_by_entry_invalid"
    if visibility == "shared" and known_by:
        return "shared_with_knowers"
    if visibility == "restricted" and not known_by:
        return "restricted_without_knowers"
    if field == "secrets" and visibility != "restricted":
        return "secrets_not_restricted"
    return ""


def parse_npc_payload(
    raw: str, *, primary_name: str, user_name: str, diagnostics: dict[str, object] | None = None
) -> tuple[list[NpcExtractionGroup], bool]:
    try:
        payload = json.loads(unfence_json(raw))
    except (TypeError, json.JSONDecodeError):
        record_tracker_contract(raw, "npc", False, "malformed_json", diagnostics)
        return [], False
    groups_raw = payload.get("npcs") if isinstance(payload, dict) else payload
    if not isinstance(groups_raw, list):
        code = "npc_root_missing" if isinstance(payload, dict) and "npcs" not in payload else "npc_root_type"
        record_tracker_contract(raw, "npc", False, code, diagnostics)
        return [], False

    blocked = {normalize_npc_name(primary_name), normalize_npc_name(user_name)}
    groups: list[NpcExtractionGroup] = []
    supporting_candidate_seen = False
    invalid_mode_seen = False
    invalid_operations_seen = len(groups_raw) > _limits.NPC_EXTRACTION_MAX_GROUPS
    audience_rejection_code = ""
    for item in groups_raw[: _limits.NPC_EXTRACTION_MAX_GROUPS]:
        if not isinstance(item, dict):
            invalid_operations_seen = True
            continue
        name = " ".join(str(item.get("name") or "").split()).strip()[: _limits.NPC_NAME_MAX_CHARS]
        if not name:
            invalid_operations_seen = True
            continue
        if normalize_npc_name(name) in blocked:
            continue
        supporting_candidate_seen = True
        aliases_raw = item.get("aliases") or []
        aliases = []
        if isinstance(aliases_raw, list):
            seen = set()
            for alias in aliases_raw[: _limits.NPC_MAX_ALIASES]:
                value = " ".join(str(alias or "").split()).strip()[: _limits.NPC_NAME_MAX_CHARS]
                key = normalize_npc_name(value)
                if value and key and key != normalize_npc_name(name) and key not in blocked and key not in seen:
                    seen.add(key)
                    aliases.append(value)

        operations_raw = item.get("operations")
        if not isinstance(operations_raw, list):
            invalid_operations_seen = True
            continue
        if not operations_raw or len(operations_raw) > _limits.NPC_EXTRACTION_MAX_OPERATIONS_PER_GROUP:
            invalid_operations_seen = True
        operations: list[NpcOperation] = []
        for raw_op in operations_raw[: _limits.NPC_EXTRACTION_MAX_OPERATIONS_PER_GROUP]:
            if not isinstance(raw_op, dict):
                invalid_operations_seen = True
                continue
            field = str(raw_op.get("field") or "").strip().casefold()
            operation = str(raw_op.get("op") or "").strip().casefold()
            mode = str(raw_op.get("mode") or "").strip().casefold()
            visibility_raw = raw_op.get("visibility")
            visibility = (
                "restricted"
                if visibility_raw is None and field == "secrets"
                else str(visibility_raw or "shared").strip().casefold()
            )
            known_by_raw = raw_op.get("known_by", [])
            if audience_code := _audience_rejection_code(raw_op, field, visibility, known_by_raw):
                audience_rejection_code = audience_rejection_code or audience_code
                continue
            known_by = tuple(" ".join(value.split()).strip()[: _limits.NPC_NAME_MAX_CHARS] for value in known_by_raw)
            value = raw_op.get("value")
            if isinstance(value, str):
                value = value[: _limits.NPC_FIELD_VALUE_MAX_CHARS]
            elif isinstance(value, list) and all(isinstance(part, str) for part in value):
                if field not in {"secrets", "status"} or operation != "set":
                    invalid_operations_seen = True
                    continue
                value = [str(part)[: _limits.NPC_FIELD_VALUE_MAX_CHARS] for part in value[: _limits.NPC_LIST_MAX_ITEMS]]
            else:
                invalid_operations_seen = True
                continue
            if (expected_mode := _expected_mode(field)) is not None and mode != expected_mode:
                invalid_mode_seen = True
            candidate = NpcOperation(field, operation, value, mode, visibility, known_by)
            try:
                validated = validate_npc_operation(candidate)
            except ValueError:
                validated = None
            if validated is not None:
                operations.append(validated)
            else:
                invalid_operations_seen = True
        if operations:
            groups.append(NpcExtractionGroup(name, tuple(aliases), tuple(operations)))
    # Never publish a partial supporting-character extraction as complete coverage.
    valid = (
        (bool(groups) or not supporting_candidate_seen)
        and not invalid_operations_seen
        and not audience_rejection_code
        and not invalid_mode_seen
    )
    code = (
        "accepted"
        if valid
        else "npc_mode_invalid"
        if invalid_mode_seen
        else "invalid_audience"
        if audience_rejection_code
        else "npc_operations_invalid"
    )
    record_tracker_contract(raw, "npc", valid, code, diagnostics, audience_code=audience_rejection_code)
    return groups if valid else [], valid
