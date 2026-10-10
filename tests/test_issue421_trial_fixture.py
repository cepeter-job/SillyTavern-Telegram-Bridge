"""New trial inputs are synthetic, bounded and exercise native helper contracts."""

import pytest

from tools.issue421_helper_fixture import SCENARIOS, capture_case


@pytest.mark.parametrize("kind", ["episodes", "npc", "scene", "curator", "story"])
def test_native_fixture_captures_one_request_without_network(tmp_path, kind):
    result = capture_case(tmp_path, SCENARIOS[0], kind)
    assert result["status"] == "complete"
    assert len(result["calls"]) == 1
    call = result["calls"][0]
    assert call["messages"]
    assert call["settings"]["max_tokens"] <= 1400
    assert call["force_non_stream"] is True
    assert (call["settings"].get("json_once") is True) == (kind != "story")
    assert result["source_sha256"]


def test_scenarios_cover_negation_branch_and_indonesian():
    assert len(SCENARIOS) == 2
    assert {case["language"] for case in SCENARIOS} == {"en", "id"}
    assert all(case["required_facts"] and case["forbidden_inferences"] for case in SCENARIOS)
