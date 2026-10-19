"""Audience subcodes explain rejected NPC contracts without retaining private output."""

import json
import logging

import pytest
from application_test_setup import make_test_provider_port
from memory_runtime_test_support import isolated_memory_runtime as isolated_memory_runtime
from session_test_support import session_db as session_db
from test_durable_memory_workers import add

from bridge.extraction_contracts import record_tracker_contract, tracker_repair_instruction
from bridge.memory_store import claim_jobs
from bridge.memory_workers import run_memory_claim
from bridge.npc_extraction_format import parse_npc_payload
from bridge.provider_completion import StructuredOutputCapped
from bridge.provider_errors import ProviderRequestError
from bridge.simulation_service import SimulationService


def operation(**changes):
    return dict(field="role", op="set", value="Archivist", mode="mutable", visibility="shared", known_by=[]) | changes


def payload(operations):
    return {"npcs": [{"name": "Maya", "aliases": [], "operations": operations}], "simulation": {}}


def events(caplog, name):
    return [r.diagnostic_fields for r in caplog.records if getattr(r, "diagnostic_fields", {}).get("event") == name]


@pytest.mark.parametrize(
    "changes,subcode,guidance",
    [
        ({"visibility": None}, "visibility_type_invalid", "JSON string"),
        ({"visibility": "PRIVATE_ENUM"}, "visibility_value_invalid", "Use only visibility"),
        ({"known_by": "PRIVATE_KNOWER"}, "known_by_type_invalid", "JSON array"),
        (
            {"visibility": "restricted", "known_by": ["PRIVATE_KNOWER"] * 9},
            "known_by_limit_exceeded",
            "at most 8",
        ),
        ({"visibility": "restricted", "known_by": [42]}, "known_by_entry_invalid", "nonempty name string"),
        ({"known_by": ["PRIVATE_KNOWER"]}, "shared_with_knowers", "never clear knowers"),
        ({"visibility": "restricted"}, "restricted_without_knowers", "Do not switch it to shared"),
        ({"field": "secrets"}, "secrets_not_restricted", "Secrets always require restricted"),
    ],
)
def test_audience_subcodes_preserve_complete_rejection_and_private_diagnostics(changes, subcode, guidance, caplog):
    caplog.set_level(logging.INFO, logger="bridge.events")
    diagnostics = {}
    raw = json.dumps(payload([operation(), operation(value="PRIVATE_VALUE", **changes)]))
    assert parse_npc_payload(raw, primary_name="Primary", user_name="User", diagnostics=diagnostics) == ([], False)
    assert diagnostics["rejection_code"] == "invalid_audience"
    assert diagnostics["audience_rejection_code"] == subcode
    assert events(caplog, "npc.contract_parsed")[0]["audience_rejection_code"] == subcode
    instruction = tracker_repair_instruction(
        "invalid_audience", "accepted", repair_npcs=True, npc_audience_code=subcode
    )
    assert subcode in instruction and guidance in instruction
    assert "PRIVATE" not in instruction + repr(diagnostics) + caplog.text


@pytest.mark.parametrize(
    "audience,field,expected_visibility,expected_knowers",
    [
        ({}, "role", "shared", ()),
        ({"visibility": ""}, "role", "shared", ()),
        ({"visibility": " SHARED ", "known_by": []}, "role", "shared", ()),
        ({"known_by": ["  Exact   Knower  "]}, "secrets", "restricted", ("Exact Knower",)),
    ],
)
def test_existing_audience_defaults_and_normalization_remain_valid(
    audience, field, expected_visibility, expected_knowers
):
    candidate = operation(field=field)
    del candidate["visibility"], candidate["known_by"]
    candidate.update(audience)
    diagnostics = {}
    groups, valid = parse_npc_payload(
        json.dumps(payload([candidate])), primary_name="Primary", user_name="User", diagnostics=diagnostics
    )
    assert valid
    assert groups[0].operations[0].visibility == expected_visibility
    assert groups[0].operations[0].known_by == expected_knowers
    assert "audience_rejection_code" not in diagnostics


def test_first_audience_subcode_is_deterministic_without_changing_mode_priority():
    diagnostics = {}
    raw = json.dumps(
        payload(
            [
                operation(mode="fixed"),
                operation(visibility=None, known_by="PRIVATE_KNOWER"),
                operation(known_by=["PRIVATE_KNOWER"]),
            ]
        )
    )
    assert parse_npc_payload(raw, primary_name="Primary", user_name="User", diagnostics=diagnostics) == ([], False)
    assert diagnostics["rejection_code"] == "npc_mode_invalid"
    assert diagnostics["audience_rejection_code"] == "visibility_type_invalid"
    instruction = tracker_repair_instruction(
        "npc_mode_invalid", "accepted", repair_npcs=True, npc_audience_code=diagnostics["audience_rejection_code"]
    )
    assert "visibility_type_invalid" in instruction


@pytest.mark.parametrize("untrusted", ["PRIVATE_SUBCODE", {"PRIVATE_SUBCODE": True}])
def test_unknown_audience_subcode_cannot_reach_logs_or_repair(untrusted, caplog):
    caplog.set_level(logging.INFO, logger="bridge.events")
    diagnostics = {}
    record_tracker_contract("{}", "npc", False, "invalid_audience", diagnostics, audience_code=untrusted)
    instruction = tracker_repair_instruction(
        "invalid_audience", "accepted", repair_npcs=True, npc_audience_code=untrusted
    )
    assert "audience_rejection_code" not in diagnostics
    assert "PRIVATE" not in instruction + repr(diagnostics) + caplog.text


def test_reused_parser_diagnostics_do_not_keep_an_obsolete_audience_subcode():
    diagnostics = {}
    raw = json.dumps(payload([operation(known_by=["PRIVATE_KNOWER"])]))
    parse_npc_payload(raw, primary_name="Primary", user_name="User", diagnostics=diagnostics)
    assert diagnostics["audience_rejection_code"] == "shared_with_knowers"
    assert parse_npc_payload('{"npcs":[]}', primary_name="Primary", user_name="User", diagnostics=diagnostics) == (
        [],
        True,
    )
    assert "audience_rejection_code" not in diagnostics


@pytest.mark.parametrize("repair", ["accepted", "invalid", "timeout", "capped", "rate_limit"])
def test_audience_repair_reports_effective_state_and_preserves_independent_simulation(session_db, caplog, repair):
    settings, db, session = session_db
    add(db, "Maya is an archivist. The user receives a raincoat.")
    initial = payload([operation(known_by=["PRIVATE_KNOWER"])])
    initial["simulation"] = {"actor": {"inventory_add": ["Raincoat"]}}
    calls = []
    caplog.set_level(logging.INFO, logger="bridge.events")

    def generate(*args, **kwargs):
        calls.append((args[2], kwargs["settings"]))
        if len(calls) == 1:
            return json.dumps(initial)
        if repair == "timeout":
            raise TimeoutError("PRIVATE_EXCEPTION")
        if repair == "capped":
            raise StructuredOutputCapped("PRIVATE_CAPPED_RESPONSE")
        if repair == "rate_limit":
            raise ProviderRequestError("synthetic::model", "rate_limit", 429)
        if repair == "invalid":
            return json.dumps(payload([operation(visibility="restricted")]) | {"simulation": []})
        return json.dumps(payload([operation()]))

    result = run_memory_claim(
        db,
        claim_jobs(db, layers=("npc",))[0],
        session,
        {"name": "Primary"},
        provider_port=make_test_provider_port(generate_backend=generate),
        app_settings=settings,
    )
    assert len(calls) == 2
    assert calls[0][0][-1] == calls[1][0][-1]
    assert all(options["max_tokens"] == 1400 and options["json_once"] is True for _, options in calls)
    assert "shared_with_knowers" in calls[1][0][0]["content"]
    assert "PRIVATE" not in repr(calls[1]) + caplog.text
    start = events(caplog, "tracker.repair_start")
    finish = events(caplog, "tracker.repair_finish")
    assert len(start) == len(finish) == 1
    assert start[0]["npc_rejection_code"] == "invalid_audience"
    assert start[0]["npc_audience_rejection_code"] == "shared_with_knowers"
    assert finish[0]["accepted"] is (repair == "accepted")
    assert finish[0]["simulation_valid"] is True
    assert finish[0]["simulation_rejection_code"] == "accepted"
    if repair in {"timeout", "capped", "rate_limit"}:
        expected_reason = {"timeout": "work_failed", "capped": "output_capped", "rate_limit": "rate_limit"}[repair]
        assert finish[0]["reason"] == expected_reason
    if repair == "accepted":
        assert finish[0]["npc_rejection_code"] == "accepted"
        assert "npc_audience_rejection_code" not in finish[0]
    else:
        expected = "restricted_without_knowers" if repair == "invalid" else "shared_with_knowers"
        assert finish[0]["npc_rejection_code"] == "invalid_audience"
        assert finish[0]["npc_audience_rejection_code"] == expected
        assert db.execute("SELECT count(*) FROM npc_field_history").fetchone() == (0,)
    assert result == ("complete" if repair == "accepted" else "invalid_npc_output")
    coverage = db.execute("SELECT covered_id FROM memory_layer_state WHERE layer='npc'").fetchone()[0]
    assert (coverage > 0) is (repair == "accepted")
    actor = SimulationService().state(db, "chat", "s1", "actor", "user")
    assert actor is not None and actor["inventory"][0]["name"] == "Raincoat"
