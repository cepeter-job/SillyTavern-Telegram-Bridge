"""External tracker contracts reject partial state without exposing provider content."""

import json
import logging

import pytest
from application_test_setup import make_test_provider_port
from memory_runtime_test_support import isolated_memory_runtime as isolated_memory_runtime
from session_test_support import session_db as session_db
from test_durable_memory_workers import add

from bridge.extraction_contracts import REJECTION_CODES, tracker_repair_instruction
from bridge.memory_store import claim_jobs
from bridge.memory_workers import run_memory_claim
from bridge.npc_extraction_format import parse_npc_payload
from bridge.simulation_extraction import normalize_simulation_payload, parse_simulation_payload


def operation(**changes):
    return dict(field="role", op="set", value="Archivist", mode="mutable", visibility="shared", known_by=[]) | changes


def npc_payload(operations):
    return {"npcs": [{"name": "Maya", "aliases": [], "operations": operations}], "simulation": {}}


@pytest.mark.parametrize(
    "bad,code",
    [
        (operation(known_by=["PRIVATE_KNOWER"]), "invalid_audience"),
        (operation(known_by="PRIVATE_KNOWER"), "invalid_audience"),
        (operation(known_by=None), "invalid_audience"),
        (operation(known_by=[42]), "invalid_audience"),
        (operation(visibility="restricted", known_by=[""]), "invalid_audience"),
        (operation(visibility=None), "invalid_audience"),
        (operation(visibility=42), "invalid_audience"),
        (operation(field="secrets", visibility="shared"), "invalid_audience"),
        (operation(mode="fixed"), "npc_mode_invalid"),
        (operation(op="PRIVATE_OPERATION"), "npc_operations_invalid"),
        (operation(value={"PRIVATE_VALUE": True}), "npc_operations_invalid"),
        (operation(field="PRIVATE_FIELD"), "npc_operations_invalid"),
        (operation(value=["text", 42]), "npc_operations_invalid"),
        ("PRIVATE_OPERATION", "npc_operations_invalid"),
    ],
)
def test_mixed_npc_operations_fail_closed(bad, code, caplog):
    caplog.set_level(logging.INFO, logger="bridge.events")
    diagnostics = {}
    result = parse_npc_payload(
        json.dumps(npc_payload([operation(), bad])), primary_name="Primary", user_name="User", diagnostics=diagnostics
    )
    assert result == ([], False)
    assert diagnostics["rejection_code"] == code
    assert "PRIVATE" not in repr(diagnostics) + caplog.text


def test_invalid_supporting_group_cannot_hide_behind_valid_group():
    payload = npc_payload([operation()])
    payload["npcs"].append({"name": "Jon", "operations": [operation(op="invalid")]})
    assert parse_npc_payload(json.dumps(payload), primary_name="Primary", user_name="User") == ([], False)


@pytest.mark.parametrize("name", ["Primary", "User"])
def test_intentional_exclusions_and_empty_npcs_remain_valid(name):
    payload = npc_payload([operation(known_by=42)])
    payload["npcs"][0]["name"] = name
    assert parse_npc_payload(json.dumps(payload), primary_name="Primary", user_name="User") == ([], True)
    assert parse_npc_payload('{"npcs":[]}', primary_name="Primary", user_name="User") == ([], True)


SIMULATION_REJECTIONS = [
    ({"quest": [{"id": "PRIVATE_ID"}]}, "simulation_unsupported_field", "quests"),
    ({"actor": {"inventory": ["PRIVATE_ITEM"]}}, "simulation_unsupported_field", "inventory_add"),
    ({"quests": [{"id": "q", "PRIVATE_FIELD": "PRIVATE_VALUE"}]}, "simulation_unsupported_field", "supported"),
    ({"relationships": [{"npc": "Maya", "PRIVATE_FIELD": 1}]}, "simulation_unsupported_field", "supported"),
    ({"actor": {"skills_add": [{"name": "Skill", "PRIVATE_FIELD": 1}]}}, "simulation_unsupported_field", "supported"),
    ({"actor": []}, "simulation_type_invalid", "object"),
    ({"quests": {}}, "simulation_type_invalid", "array"),
    ({"factions": [{"name": "Guild", "morale": 7}]}, "simulation_type_invalid", "strings"),
    ({"relationships": [{"npc": "Maya", "apology": "false"}]}, "simulation_type_invalid", "booleans"),
    ({"quests": [{"id": "q", "kind": "PRIVATE_ENUM"}]}, "simulation_enum_invalid", "main|side"),
    ({"tasks": [{"id": "t", "status": "PRIVATE_ENUM"}]}, "simulation_enum_invalid", "active"),
    ({"agendas": [{"npc": "Maya", "status": "PRIVATE_ENUM"}]}, "simulation_enum_invalid", "paused"),
    ({"foreshadowing": [{"id": "f", "status": "PRIVATE_ENUM"}]}, "simulation_enum_invalid", "planted"),
    ({"actor": {"inventory_add": ["Key"], "inventory_remove": ["key"]}}, "simulation_actor_conflict", "both"),
]


@pytest.mark.parametrize("simulation,code,guidance", SIMULATION_REJECTIONS)
def test_simulation_precise_safe_rejections(simulation, code, guidance, caplog):
    caplog.set_level(logging.INFO, logger="bridge.events")
    diagnostics = {}
    assert parse_simulation_payload(json.dumps({"npcs": [], "simulation": simulation}), diagnostics=diagnostics) == (
        {},
        False,
    )
    assert diagnostics["rejection_code"] == code
    assert code in REJECTION_CODES
    instruction = tracker_repair_instruction("accepted", code, repair_npcs=False)
    assert guidance in instruction
    assert "PRIVATE" not in instruction + repr(diagnostics) + caplog.text


def test_unknown_simulation_wrapper_is_rejected():
    diagnostics = {}
    assert parse_simulation_payload('{"simulation":{},"quest":["PRIVATE"]}', diagnostics=diagnostics) == ({}, False)
    assert diagnostics["rejection_code"] == "simulation_unsupported_field"


def test_external_empty_simulation_and_internal_normalization_compatibility():
    result, valid = parse_simulation_payload('{"simulation":{}}')
    assert valid and result["quests"] == [] and result["actor"] == {}
    assert normalize_simulation_payload({"quest": ["legacy"]})["quests"] == []
    assert parse_simulation_payload('{"simulation":{"quest":[]}}')[1]
    # Faction relation names are dynamic data, not extraction field names.
    assert parse_simulation_payload('{"simulation":{"factions":[{"name":"Guild","relations":{"Rivals":"cold"}}]}}')[1]


@pytest.mark.parametrize("repair_succeeds", [False, True])
@pytest.mark.parametrize("kind", ["npc", "simulation"])
def test_partial_extraction_uses_one_canonical_repair_and_preserves_coverage(
    session_db, monkeypatch, caplog, repair_succeeds, kind
):
    from bridge import npc_extraction

    settings, db, session = session_db
    monkeypatch.setattr(npc_extraction, "persona_name", lambda *a, **k: "User")
    add(db, "CANONICAL_SOURCE: Maya is an archivist.")
    payload = npc_payload([operation(), operation(known_by=["PRIVATE_KNOWER"])])
    if kind == "simulation":
        payload = {"npcs": [], "simulation": {"actor": {"inventory": ["PRIVATE_ITEM"]}}}
    calls = []
    caplog.set_level(logging.INFO, logger="bridge.events")

    def generate(*args, **kwargs):
        calls.append(args[2])
        if repair_succeeds and len(calls) == 2:
            return json.dumps(npc_payload([operation()]))
        return json.dumps(payload)

    result = run_memory_claim(
        db,
        claim_jobs(db, layers=("npc",))[0],
        session,
        {"name": "Alice"},
        provider_port=make_test_provider_port(generate_backend=generate),
        app_settings=settings,
    )
    assert len(calls) == 2
    assert calls[0][-1] == calls[1][-1]
    assert "PRIVATE" not in repr(calls[1]) + caplog.text
    assert result == ("complete" if repair_succeeds else "invalid_npc_output")
    coverage = db.execute("SELECT covered_id FROM memory_layer_state WHERE layer='npc'").fetchone()[0]
    assert (coverage > 0) is repair_succeeds
    if not repair_succeeds:
        assert db.execute("SELECT count(*) FROM npc_field_history").fetchone() == (0,)
