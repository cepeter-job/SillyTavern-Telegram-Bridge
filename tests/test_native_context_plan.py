"""Native experiment plans freeze matched inputs before any provider dispatch."""

import json
from pathlib import Path

import pytest

from tools.evaluate_native_context import build_native_plan, revalidate_case
from tools.native_context_fixture import declared_cases


def test_workload_declares_controls_and_weights_without_dropping_hard_cases():
    cases = declared_cases()
    assert len(cases) == 6
    assert len({x["case_id"] for x in cases}) == 6
    assert sum(x["weight"] for x in cases) == pytest.approx(1)
    assert [x["expected_candidate"] for x in cases].count("no_savings") == 2
    assert any(x["language"] == "id" for x in cases)
    assert all(x["review_canon"] for x in cases)


@pytest.fixture(scope="module")
def native_plan(tmp_path_factory):
    cases = declared_cases()
    subset = [dict(cases[0], history=cases[0]["history"][:28], weight=0.5), dict(cases[-1], weight=0.5)]
    directory = tmp_path_factory.mktemp("native-plan")
    return directory, build_native_plan(directory, model="nano-gpt::z-ai/glm-5.2", max_output_tokens=1000, cases=subset)


def test_plan_uses_real_native_checkpoints_and_equal_provider_settings(native_plan):
    directory, plan = native_plan
    assert len(plan["cases"]) == 2
    assert plan["network"]["requests"] == 0
    assert plan["production_activation_allowed"] is False
    assert len(plan["plan_sha256"]) == 64
    for case in plan["cases"]:
        left, right = case["variants"]["baseline"], case["variants"]["candidate"]
        assert left["model"] == right["model"]
        assert left["settings"] == right["settings"] == {"max_tokens": 1000, "temperature": 0.7}
        assert left["messages"][0] == right["messages"][0]
        assert left["messages"][-1] == right["messages"][-1]
        assert revalidate_case(directory, case)["all_source_evidence_preserved"]
        assert Path(directory, case["snapshot_file"]).exists()
    assert plan["cases"][0]["selection"]["encoded_turns"] > 0
    assert plan["cases"][1]["variants"]["baseline"]["messages"] == plan["cases"][1]["variants"]["candidate"]["messages"]


def test_plan_refuses_mutated_reference_prompt_even_with_old_saved_proof(native_plan):
    directory, plan = native_plan
    copied = json.loads(json.dumps(plan["cases"][0]))
    copied["variants"]["candidate"]["messages"][0]["content"] += " Invent consent."
    with pytest.raises(ValueError):
        revalidate_case(directory, copied)


@pytest.mark.parametrize("invalid_receipt", [False, True])
def test_revalidation_closes_snapshot_connection_on_success_and_error(native_plan, monkeypatch, invalid_receipt):
    import sqlite3

    from tools import evaluate_native_context

    directory, plan = native_plan
    case = json.loads(json.dumps(plan["cases"][0]))
    if invalid_receipt:
        case["receipt_sha256"] = "0" * 64
    original_connect = sqlite3.connect
    opened = []

    def connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(evaluate_native_context.sqlite3, "connect", connect)
    try:
        if invalid_receipt:
            with pytest.raises(ValueError, match="native_receipt_changed"):
                revalidate_case(directory, case)
        else:
            assert revalidate_case(directory, case)["all_source_evidence_preserved"]
        assert len(opened) == 1
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            opened[0].execute("SELECT 1")
    finally:
        for connection in opened:
            connection.close()
