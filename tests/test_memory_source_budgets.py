"""Bounded first-request allocation uses complete source and prior JSON bytes."""

import json
from dataclasses import replace

import pytest
from settings_test_support import make_test_settings
from test_story_memory_scope import append
from test_story_memory_scope import db as db

from bridge import memory, scene_state
from bridge.memory_store import next_source_segment
from bridge.provider_completion import StructuredOutputCapped
from bridge.provider_port import ProviderPort
from bridge.summary_block_coalescing import summary_length_contract

SESSION = {"session_id": "s", "model_id": "m"}
BLOCK = {"text": "Mira hides the key.", "visibility": "restricted", "known_by": ["mira"]}


def extract(db, tmp_path, layer, previous, source, generate):
    kwargs = {"provider_port": ProviderPort(generate), "app_settings": make_test_settings(home=tmp_path)}
    if layer == "summary":
        return memory.extract_summary_segment(db, "c", SESSION, previous, source, **kwargs)
    return scene_state.extract_scene_segment(db, "c", SESSION, "Mira", previous, source, **kwargs)


def payload(layer):
    result = {"blocks": [BLOCK]}
    if layer == "scene":
        result["state"] = {"facts": [BLOCK["text"]]}
    return result


@pytest.mark.parametrize("layer,floor,ceiling,copies", [("summary", 1200, 4096, 1), ("scene", 1000, 8192, 2)])
@pytest.mark.parametrize(
    "source_text,prior_text",
    [
        ("short", ""),
        ("x" * 3600, ""),
        ("界" * 2000, ""),
        ("x" * 12000, ""),
        ("界" * 12000, ""),
        ("short", "界" * 2000),
        ("x" * 3600, "界" * 600),
    ],
    ids=["small", "ascii-source", "utf8-source", "full-ascii", "full-utf8", "utf8-prior", "combined"],
)
def test_first_request_reserves_source_and_prior_envelope(
    db, tmp_path, layer, floor, ceiling, copies, source_text, prior_text
):
    append(db, source_text)
    source = next_source_segment(db, "c", "s", layer)
    assert source is not None and source.content == source_text
    previous = {"blocks": [{**BLOCK, "text": prior_text}]} if prior_text else {"blocks": []}
    if layer == "scene":
        previous["state"] = {"facts": [prior_text]} if prior_text else {}
    before = json.dumps(previous, ensure_ascii=False)
    expected = min(
        ceiling, max(floor, (len(before.encode("utf-8")) + copies * len(source_text.encode("utf-8")) + 2) // 3 + 320)
    )
    calls = []

    def generate(_key, _model, messages, **kwargs):
        calls.append((messages, kwargs["settings"]))
        # Model a provider that truncates below the needed output allocation.
        if kwargs["settings"]["max_tokens"] < expected:
            raise StructuredOutputCapped('{"blocks":')
        return json.dumps(payload(layer))

    result = extract(db, tmp_path, layer, previous, source, generate)
    assert result == payload(layer)
    assert len(calls) == 1
    messages, settings = calls[0]
    assert settings["max_tokens"] == expected
    assert settings["json_once"] is True
    assert messages[-1]["content"].endswith("Canonical source part:\n" + source_text)
    assert before in messages[-1]["content"]
    assert json.dumps(previous, ensure_ascii=False) == before
    assert db.execute("SELECT count(*) FROM memory_segments").fetchone() == (0,)


@pytest.mark.parametrize("layer", ["summary", "scene"])
@pytest.mark.parametrize("repair_succeeds", [False, True])
def test_source_aware_allocation_keeps_single_canonical_repair(db, tmp_path, layer, repair_succeeds):
    append(db, "界" * 4000)
    source = next_source_segment(db, "c", "s", layer)
    calls = []

    def generate(_key, _model, messages, **kwargs):
        calls.append((messages, kwargs["settings"]))
        if len(calls) == 1 or not repair_succeeds:
            raise StructuredOutputCapped('{"blocks":')
        return json.dumps(payload(layer))

    if repair_succeeds:
        assert extract(db, tmp_path, layer, {"blocks": []}, source, generate) == payload(layer)
    else:
        with pytest.raises(StructuredOutputCapped):
            extract(db, tmp_path, layer, {"blocks": []}, source, generate)
    assert len(calls) == 2
    assert calls[0][1]["max_tokens"] > (1200 if layer == "summary" else 1000)
    assert calls[0][1] == calls[1][1]
    assert calls[1][0][1:] == calls[0][0]
    assert calls[0][0][-1]["content"].endswith(source.content)
    assert db.execute("SELECT count(*) FROM memory_segments").fetchone() == (0,)
    assert db.execute("SELECT max(covered_id) FROM memory_layer_state").fetchone() == (0,)


@pytest.mark.parametrize("layer", ["summary", "scene"])
def test_oversized_source_remains_rejected_before_inference(db, tmp_path, layer):
    append(db, "x")
    source = replace(next_source_segment(db, "c", "s", layer), content="x" * 12001)
    with pytest.raises(ValueError, match="bounded source part"):
        extract(db, tmp_path, layer, {"blocks": []}, source, lambda *a, **k: pytest.fail("Unexpected inference"))


@pytest.mark.parametrize("prior_size", [0, 10000])
def test_summary_prompts_bound_individual_block_text(prior_size):
    contract = summary_length_contract({"blocks": [{**BLOCK, "text": "x" * prior_size}]})
    assert "Each individual block text must be at most 5,000 characters" in contract
    assert "12,000" in contract
