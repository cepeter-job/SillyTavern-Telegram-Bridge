"""Canonical helper requests: explicit schemas, extractor context and capped JSON."""

import json
from types import SimpleNamespace

import pytest
from settings_test_support import make_test_settings
from test_memory_json_transport import routed_provider

from bridge import npc_extraction, scene_state, simulation_context, simulation_prompt
from bridge.memory_artifact_store import parse_classified_response
from bridge.memory_response import MemorySourceChanged, generate_memory_response, memory_failure_code
from bridge.npc_extraction_format import parse_npc_payload
from bridge.simulation_extraction import parse_simulation_payload


@pytest.fixture
def extraction_inputs(tmp_path, monkeypatch):
    # No SQL, native configuration or actual provider transport is involved.
    settings = make_test_settings(home=tmp_path)
    db = SimpleNamespace(in_transaction=False)
    session = {"session_id": "synthetic", "persona_id": "", "model_id": "nano-gpt::z-ai/glm-5.2"}
    content = "Mira remains the archivist at the library. The user receives a key."
    source = SimpleNamespace(content=content, role="assistant", start_id=2, start_offset=0, end_offset=len(content))
    for owner in (npc_extraction, scene_state):
        monkeypatch.setattr(owner, "get_generation_settings", lambda *a: {"top_p": 1.0})
        monkeypatch.setattr(owner, "utility_reasoning_for_session", lambda *a: 0)
        monkeypatch.setattr(owner, "task_model_for_session", lambda *a, **k: "nano-gpt::z-ai/glm-5.2")
    monkeypatch.setattr(npc_extraction, "persona_name", lambda *a, **k: "User")
    monkeypatch.setattr(npc_extraction, "_existing_state_text", lambda *a, **k: "[]")
    monkeypatch.setattr(npc_extraction, "extraction_tracker_context", lambda *a, **k: "")
    return db, session, source, settings


def response(content, reason, cap):
    return {
        "choices": [{"message": {"content": content}, "finish_reason": reason}],
        "usage": {"completion_tokens": cap, "completion_tokens_details": {"reasoning_tokens": 0}},
    }


def helper_call(kind, inputs, port):
    db, session, source, settings = inputs
    arguments = (db, "chat", session, {"name": "Primary"} if kind == "npc" else "Primary", {}, source)
    extract = npc_extraction.extract_npc_segment if kind == "npc" else scene_state.extract_scene_segment
    return extract(*arguments, provider_port=port, app_settings=settings)


def nanogpt_port(tmp_path, monkeypatch, payloads):
    return routed_provider(
        tmp_path, monkeypatch, payloads, provider="nano-gpt", host="nano-gpt.com", model_id="z-ai/glm-5.2"
    )


@pytest.mark.parametrize(
    "kind,cap,complete",
    [("npc", 1400, '{"npcs":[],"simulation":{}}'), ("scene", 1000, '{"state":{},"blocks":[]}')],
)
def test_complete_looking_capped_json_cannot_complete_without_repair(
    tmp_path, monkeypatch, extraction_inputs, kind, cap, complete
):
    port, sent = nanogpt_port(
        tmp_path, monkeypatch, [response(complete, "length", cap), response(complete, "stop", 12)]
    )
    result = helper_call(kind, extraction_inputs, port)
    assert result["npcs" if kind == "npc" else "state"] == ([] if kind == "npc" else {})
    assert len(sent) == 2
    assert sent[0]["messages"][-1] == sent[1]["messages"][-1]
    assert all(call["max_tokens"] == cap and call["response_format"] == {"type": "json_object"} for call in sent)
    assert all(call["stream"] is False and not any(m["role"] == "assistant" for m in call["messages"]) for call in sent)
    assert "output_capped" in sent[1]["messages"][0]["content"]


@pytest.mark.parametrize(
    "kind,cap,complete",
    [("npc", 1400, '{"npcs":[],"simulation":{}}'), ("scene", 1000, '{"state":{},"blocks":[]}')],
)
@pytest.mark.parametrize("initial_reason", ["stop", "length"])
def test_capped_repair_cannot_return_a_publishable_result(
    tmp_path, monkeypatch, extraction_inputs, kind, cap, complete, initial_reason
):
    port, sent = nanogpt_port(
        tmp_path, monkeypatch, [response("{}", initial_reason, cap), response(complete, "length", cap)]
    )
    with pytest.raises(ValueError, match="output cap") as error:
        helper_call(kind, extraction_inputs, port)
    assert memory_failure_code(error.value) == "output_capped"
    assert len(sent) == 2
    assert sent[0]["messages"][-1] == sent[1]["messages"][-1]
    assert all(call["max_tokens"] == cap for call in sent)


def test_extraction_context_does_not_instruct_the_helper_to_suppress_trackers(monkeypatch):
    db = SimpleNamespace(in_transaction=True)
    monkeypatch.setattr(simulation_context, "context_cutoff", lambda *a: 7)
    monkeypatch.setattr(
        simulation_context,
        "load_states_as_of",
        lambda *a, **k: [("faction", "watch", {"goal": "Guard the gate", "morale": "steady"}, 7)],
    )
    monkeypatch.setattr(simulation_context, "canonical_plot_context", lambda db, chat, session, value, cutoff: value)
    monkeypatch.setattr(simulation_context, "list_checks", lambda *a, **k: [])
    monkeypatch.setattr(simulation_prompt, "list_arc_rows", lambda *a, **k: [])
    monkeypatch.setattr(simulation_prompt, "list_narrative_threads", lambda *a, **k: [])
    monkeypatch.setattr(simulation_prompt, "list_checks", lambda *a, **k: [])
    extraction = simulation_prompt.extraction_tracker_context(db, "chat", "synthetic", 7)
    story = simulation_context.simulation_context_for_prompt(db, "chat", "synthetic", through_rowid=7)
    assert "never print trackers" not in extraction
    assert "never print trackers" in story
    assert "goal=Guard the gate morale=steady" in extraction
    assert extraction.splitlines()[1:] == story.splitlines()[1:]


def test_actor_collection_object_is_invalid_but_explicit_array_is_valid():
    # The normal-stop failure class is independently reproducible; do not coerce it.
    invalid = {"simulation": {"actor": {"inventory_add": {"name": "Key"}}}}
    assert parse_simulation_payload(json.dumps(invalid)) == ({}, False)
    valid = {"simulation": {"actor": {"inventory_add": [{"name": "Key"}]}}}
    payload, accepted = parse_simulation_payload(json.dumps(valid))
    assert accepted and payload["actor"]["inventory_add"][0]["name"] == "Key"


def test_faction_morale_requires_text():
    assert parse_simulation_payload(json.dumps({"simulation": {"factions": [{"name": "Watch", "morale": 2}]}})) == (
        {},
        False,
    )
    payload, accepted = parse_simulation_payload(
        json.dumps({"simulation": {"factions": [{"name": "Watch", "morale": "steady"}]}})
    )
    assert accepted and payload["factions"][0]["morale"] == "steady"


def json_examples(messages):
    examples = []
    decoder = json.JSONDecoder()
    for message in messages:
        if message["role"] != "system":
            continue
        content = message["content"]
        for index, char in enumerate(content):
            if char != "{":
                continue
            try:
                example, _ = decoder.raw_decode(content[index:])
            except ValueError:
                continue
            if isinstance(example, dict) and ("npcs" in example or "simulation" in example or "state" in example):
                examples.append(example)
    return examples


@pytest.mark.parametrize("initial", [{}, {"npcs": [], "simulation": {"actor": []}}])
def test_initial_and_both_tracker_repair_paths_supply_parser_valid_typed_examples(
    tmp_path, monkeypatch, extraction_inputs, initial
):
    port, sent = nanogpt_port(
        tmp_path,
        monkeypatch,
        [response(json.dumps(initial), "stop", 5), response('{"npcs":[],"simulation":{}}', "stop", 12)],
    )
    helper_call("npc", extraction_inputs, port)
    assert len(sent) == 2
    for index, call in enumerate(sent):
        examples = json_examples(call["messages"])
        combined = index == 0 or "npcs" not in initial
        assert examples
        for example in examples:
            assert set(example) == ({"npcs", "simulation"} if combined else {"simulation"})
            assert parse_simulation_payload(json.dumps(example))[1]
            if combined:
                assert parse_npc_payload(json.dumps(example), primary_name="Primary", user_name="User")[1]
        full = [item["simulation"] for item in examples if item.get("simulation", {}).get("actor")]
        marker = 'Simulation member fragment (not a complete response): "simulation":'
        for message in call["messages"]:
            if message["role"] == "system" and marker in message["content"]:
                fragment, _ = json.JSONDecoder().raw_decode(message["content"].split(marker, 1)[1])
                assert parse_simulation_payload(json.dumps({"simulation": fragment}))[1]
                full.append(fragment)
        assert full, "Initial and repair schemas must show actor update arrays, not ambiguous placeholders"
        actor = full[0]["actor"]
        assert set(actor) == {
            "inventory_add",
            "inventory_remove",
            "skills_add",
            "skills_remove",
            "conditions_add",
            "conditions_remove",
        }
        assert all(isinstance(entries, list) for entries in actor.values())
        assert isinstance(full[0]["factions"][0]["morale"], str)


def test_capped_scene_audience_conflict_is_not_regenerated(tmp_path, monkeypatch, extraction_inputs):
    raw = '{"state":{},"blocks":[{"text":"Synthetic private fact","visibility":"shared","known_by":["Mira"]}]}'
    port, sent = nanogpt_port(tmp_path, monkeypatch, [response(raw, "length", 1000)])
    with pytest.raises(ValueError, match="conflicting restricted audience"):
        helper_call("scene", extraction_inputs, port)
    assert len(sent) == 1


def test_capped_response_cannot_bypass_source_invalidation_before_repair(tmp_path, monkeypatch):
    port, sent = nanogpt_port(tmp_path, monkeypatch, [response("{}", "length", 1000)])
    with pytest.raises(MemorySourceChanged):
        generate_memory_response(
            port.generate,
            "",
            "nano-gpt::z-ai/glm-5.2",
            [{"role": "user", "content": "Synthetic canonical source"}],
            parser=parse_classified_response,
            session_id="synthetic",
            settings={"max_tokens": 1000},
            source_valid=lambda: False,
        )
    assert len(sent) == 1


def test_capped_npc_repair_preserves_only_independently_valid_original_simulation(
    tmp_path, monkeypatch, extraction_inputs
):
    original = {"npcs": "invalid", "simulation": {"actor": {"inventory_add": ["Key"]}}}
    capped = {"npcs": [], "simulation": {"actor": {"inventory_add": ["Unaccepted item"]}}}
    port, sent = nanogpt_port(
        tmp_path,
        monkeypatch,
        [response(json.dumps(original), "stop", 40), response(json.dumps(capped), "length", 1400)],
    )
    with pytest.raises(npc_extraction.NpcExtractionMalformed) as error:
        helper_call("npc", extraction_inputs, port)
    assert error.value.simulation["actor"]["inventory_add"] == [{"name": "Key", "domain": "any", "modifier": 0}]
    assert len(sent) == 2


@pytest.mark.parametrize(
    "kind,cap,complete", [("npc", 1400, '{"npcs":[],"simulation":{}}'), ("scene", 1000, '{"state":{},"blocks":[]}')]
)
@pytest.mark.parametrize("empty", [None, ""])
def test_empty_capped_initial_uses_only_existing_repair(
    tmp_path, monkeypatch, extraction_inputs, kind, cap, complete, empty
):
    port, sent = nanogpt_port(tmp_path, monkeypatch, [response(empty, "length", cap), response(complete, "stop", 12)])
    helper_call(kind, extraction_inputs, port)
    assert len(sent) == 2
    assert sent[0]["messages"][-1] == sent[1]["messages"][-1]
    assert all(call["max_tokens"] == cap and call["stream"] is False for call in sent)


@pytest.mark.parametrize("empty", [None, ""])
def test_empty_capped_source_invalidation_suppresses_repair(tmp_path, monkeypatch, empty):
    port, sent = nanogpt_port(tmp_path, monkeypatch, [response(empty, "length", 1000)])
    with pytest.raises(MemorySourceChanged):
        generate_memory_response(
            port.generate,
            "",
            "nano-gpt::z-ai/glm-5.2",
            [{"role": "user", "content": "Synthetic canonical source"}],
            parser=parse_classified_response,
            session_id="synthetic",
            settings={"max_tokens": 1000},
            source_valid=lambda: False,
        )
    assert len(sent) == 1
