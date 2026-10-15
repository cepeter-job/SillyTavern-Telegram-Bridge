"""Repair never regenerates recognized, parseable conflicting audiences."""

import json

import pytest
from test_tracker_output_contracts import nanogpt_port, response

from bridge.episodic_extraction import parse_episodic_candidates
from bridge.memory_artifact_store import parse_classified_blocks
from bridge.memory_response import generate_memory_response, memory_failure_code

CONFLICT = {"visibility": "shared", "known_by": ["Mira"]}
FACT = {"kind": "fact", "importance": 0.9, "summary": "Synthetic fact", **CONFLICT}


def classified_blocks(raw):
    return parse_classified_blocks(json.loads(raw))


@pytest.mark.parametrize(
    "payload,parser",
    [
        ({"memories": [FACT]}, parse_episodic_candidates),
        ({"summary": "Synthetic fact", **CONFLICT}, classified_blocks),
        ({"summary": 42, **CONFLICT}, classified_blocks),
        ({"blocks": ["bad shape"], "memories": [FACT]}, classified_blocks),
        ({"memories": "bad shape", "summary": "Synthetic fact", **CONFLICT}, parse_episodic_candidates),
        ([{"text": 42, **CONFLICT}], classified_blocks),
    ],
    ids=["episodic", "legacy-summary", "malformed-summary", "mixed-memories", "mixed-summary", "legacy-list"],
)
@pytest.mark.parametrize("reason", ["length", "stop"])
def test_recognized_conflicts_never_regenerate(tmp_path, monkeypatch, caplog, payload, parser, reason):
    raw = json.dumps(payload)
    # The second synthetic response would widen the supplied audience if regeneration happened.
    repair = '{"blocks":[],"memories":[],"no_memory_reason":"No durable event"}'
    port, sent = nanogpt_port(tmp_path, monkeypatch, [response(raw, reason, 1200), response(repair, "stop", 8)])
    with pytest.raises(ValueError, match="conflicting restricted audience") as error:
        generate_memory_response(
            port.generate,
            "",
            "nano-gpt::z-ai/glm-5.2",
            [{"role": "user", "content": "Synthetic canonical source"}],
            parser=parser,
            session_id="synthetic",
            settings={"max_tokens": 1200},
        )
    assert memory_failure_code(error.value) == "invalid_audience"
    assert len(sent) == 1
    assert "Synthetic fact" not in caplog.text
    assert "Synthetic fact" not in str(error.value)


@pytest.mark.parametrize(
    "payload",
    [
        {"blocks": [], "memories": [FACT]},
        {"memories": [], "blocks": [{"text": "Synthetic fact", **CONFLICT}]},
        {"blocks": [], "summary": "Synthetic fact", **CONFLICT},
    ],
)
def test_capped_guard_checks_all_recognized_mixed_envelopes(tmp_path, monkeypatch, payload):
    port, sent = nanogpt_port(tmp_path, monkeypatch, [response(json.dumps(payload), "length", 1200)])
    with pytest.raises(ValueError, match="conflicting restricted audience") as error:
        generate_memory_response(
            port.generate,
            "",
            "nano-gpt::z-ai/glm-5.2",
            [{"role": "user", "content": "Synthetic source"}],
            parser=classified_blocks,
            session_id="synthetic",
            settings={"max_tokens": 1200},
        )
    assert memory_failure_code(error.value) == "invalid_audience"
    assert len(sent) == 1


def test_capped_episodic_repair_keeps_restricted_audience_without_replaying_output(tmp_path, monkeypatch, caplog):
    raw = json.dumps({"memories": [{**FACT, "summary": "Synthetic output canary", "visibility": "restricted"}]})
    port, sent = nanogpt_port(tmp_path, monkeypatch, [response(raw, "length", 1200), response(raw, "stop", 25)])
    result = generate_memory_response(
        port.generate,
        "",
        "nano-gpt::z-ai/glm-5.2",
        [{"role": "user", "content": "Synthetic canonical source"}],
        parser=parse_episodic_candidates,
        session_id="synthetic",
        settings={"max_tokens": 1200},
    )
    assert result[0].visibility == "restricted" and result[0].known_by == ("mira",)
    assert len(sent) == 2
    assert sent[0]["messages"][-1] == sent[1]["messages"][-1]
    assert all(call["max_tokens"] == 1200 for call in sent)
    assert "Synthetic output canary" not in json.dumps(sent[1]["messages"])
    assert "Synthetic output canary" not in caplog.text
