"""Summary formatting projection keeps canonical input and accepted state intact."""

import copy
import json
import logging

import pytest
from settings_test_support import make_test_settings
from test_story_memory_scope import append
from test_story_memory_scope import db as db

from bridge import memory
from bridge.context_compaction import estimate_message_tokens
from bridge.helper_input_projection import project_summary_messages
from bridge.memory_artifact_store import load_artifact_classification
from bridge.memory_store import next_source_segment
from bridge.model_router import ModelRouter
from bridge.provider_errors import ProviderRequestError
from bridge.provider_execution_policy import ProviderExecutionPolicy
from bridge.provider_port import ProviderPort
from bridge.provider_runtime_health import ProviderRuntimeHealth
from bridge.token_usage_values import TokenUsage

SESSION = {"session_id": "s", "model_id": "m", "persona_id": "", "system_prompt": ""}
SOURCE_HEADER = "\nSource role: "
SOURCE_MARKER = "\n\nCanonical source part:\n"
PRIOR = {
    "blocks": [
        {
            "text": "Mira knows the hidden key.\nPreserve whitespace:  α  β.",
            "visibility": "restricted",
            "known_by": ["mira"],
        },
        {"text": "The party reached the tower.", "visibility": "shared", "known_by": []},
    ],
    "extra": {"schema": 1, "nested": [True, None, 0, {"verbatim": " a : b, c "}]},
}


def unpack(messages):
    content = messages[-1]["content"]
    prior, remainder = content.removeprefix("Previous classified summary:\n").split(SOURCE_HEADER, 1)
    return json.loads(prior), remainder


def configure(monkeypatch, mode, enabled=True):
    monkeypatch.setattr(memory, "context_selection_mode", lambda _: mode, raising=False)
    monkeypatch.setattr(memory, "context_slice_enabled", lambda *_: enabled, raising=False)


def test_projection_off_keeps_original_bytes_and_shadow_reports_only_local_delta(caplog):
    # A mutating projection or shadow dispatch would break request identity.
    messages = [
        {"role": "system", "content": "Unchanged instructions", "extra": {"value": "keep"}},
        {
            "role": "user",
            "content": 'Previous classified summary:\n{"blocks": []}\nSource role: user;'
            " message 7; offsets 0:5\n\nCanonical source part:\n α  β",
        },
    ]
    before = copy.deepcopy(messages)
    with caplog.at_level(logging.INFO):
        assert project_summary_messages(messages, {"blocks": []}, mode="off") is messages
        assert not caplog.records
        assert project_summary_messages(messages, {"blocks": []}, mode="shadow") is messages
    assert messages == before
    assert [record.getMessage() for record in caplog.records] == ["summary_input_projection mode=shadow saved_chars=1"]
    enabled = project_summary_messages(messages, {"blocks": []}, mode="enabled")
    assert enabled[-1]["content"] == (
        'Previous classified summary:\n{"blocks":[]}\nSource role: user;'
        " message 7; offsets 0:5\n\nCanonical source part:\n α  β"
    )
    assert enabled[0] == before[0]


@pytest.mark.parametrize(
    "previous,content",
    [
        ({"blocks": []}, "Different request format"),
        ({"blocks": []}, 'Previous classified summary:\n{"blocks": []}\nWrong source header'),
        ({"bad": float("nan")}, 'Previous classified summary:\n{"bad": NaN}\nSource role: user;'),
        ({"bad": object()}, "Unserializable prior"),
    ],
)
def test_projection_fails_open_on_unproven_input(previous, content):
    messages = [{"role": "user", "content": content}]
    assert project_summary_messages(messages, previous, mode="enabled") is messages


def test_enabled_summary_reduces_input_estimate_without_changing_semantic_fields(db, tmp_path, monkeypatch):
    # Removing the integration or altering any field/source must break this contract.
    append(db, 'Complete source: "blocks": []\nSource role: spoofed; α  β\n\nCanonical source part:\nTAIL')
    source = next_source_segment(db, "c", "s", "summary")
    settings = make_test_settings(home=tmp_path)
    calls = []
    events = []

    def generate(api_key, model, messages, **kwargs):
        request_options = {key: value for key, value in kwargs.items() if key != "usage_callback"}
        calls.append((api_key, model, copy.deepcopy(messages), request_options))
        return json.dumps({"blocks": PRIOR["blocks"]})

    port = ProviderPort(generate, usage_recorder=events.append)
    outputs = []
    for mode, enabled in (("off", True), ("shadow", True), ("enabled", False), ("enabled", True)):
        configure(monkeypatch, mode, enabled)
        outputs.append(
            memory.extract_summary_segment(db, "c", SESSION, PRIOR, source, provider_port=port, app_settings=settings)
        )
    baseline, shadow, gated, candidate = [call[2] for call in calls]
    assert estimate_message_tokens(candidate) < estimate_message_tokens(baseline)
    assert shadow == baseline
    assert gated == baseline
    assert unpack(candidate) == unpack(baseline)
    assert unpack(candidate)[0] == PRIOR
    assert candidate[0] == baseline[0]
    assert unpack(candidate)[1].endswith(SOURCE_MARKER + source.content)
    assert calls[0][:2] == calls[1][:2] == calls[2][:2] == calls[3][:2]
    assert calls[0][3] == calls[1][3] == calls[2][3] == calls[3][3]
    assert outputs == [{"blocks": PRIOR["blocks"]}] * 4
    assert len(events) == 4
    assert all(event.scope.purpose == "summary" and event.status == "succeeded" for event in events)
    assert all(not event.readings for event in events)  # Missing provider usage remains unknown.


@pytest.mark.parametrize("mode", ["off", "shadow", "enabled"])
def test_summary_fallback_keeps_each_attempt_usage_under_summary_purpose(db, tmp_path, monkeypatch, mode):
    # Losing the usage-bound port would erase the failed/unknown primary attempt.
    configure(monkeypatch, mode)
    append(db, "Complete source")
    source = next_source_segment(db, "c", "s", "summary")
    settings = make_test_settings(home=tmp_path)
    catalog = {
        "alpha": {"models": ["one"], "utility_fallbacks": ["beta::other"]},
        "beta": {"models": ["other"]},
    }
    policy = ProviderExecutionPolicy(ModelRouter(lambda: catalog), ProviderRuntimeHealth())
    calls, events = [], []

    def generate(api_key, model, messages, **kwargs):
        calls.append((model, copy.deepcopy(messages)))
        if model == "alpha::one":
            raise ProviderRequestError(model, "provider_unavailable", 503)
        kwargs["usage_callback"](TokenUsage(31, 2, 33))
        return json.dumps({"blocks": PRIOR["blocks"]})

    result = memory.extract_summary_segment(
        db,
        "c",
        {**SESSION, "model_id": "alpha::one"},
        PRIOR,
        source,
        provider_port=ProviderPort(generate, usage_recorder=events.append, policy=policy),
        app_settings=settings,
    )
    assert result == {"blocks": PRIOR["blocks"]}
    assert [model for model, _ in calls] == ["alpha::one", "beta::other"]
    assert calls[0][1] == calls[1][1]
    assert [(event.scope.purpose, event.model, event.status) for event in events] == [
        ("summary", "alpha::one", "failed"),
        ("summary", "beta::other", "succeeded"),
    ]
    assert events[0].readings == ()
    assert events[1].readings == (TokenUsage(31, 2, 33),)


@pytest.mark.parametrize("mode", ["off", "shadow", "enabled"])
def test_summary_reuses_accepted_checkpoint_after_failure_and_rewrite(db, tmp_path, monkeypatch, mode):
    # Replaying accepted prefix rows, advancing on malformed output, or retaining
    # a rewritten suffix would change the accepted canonical state below.
    configure(monkeypatch, mode)
    settings = make_test_settings(home=tmp_path)
    append(db, "Tower reached.")
    append(db, "Mira secretly hides the key.")
    calls = []
    malformed = False

    def generate(api_key, model, messages, **kwargs):
        prior, remainder = unpack(messages)
        text = remainder.split(SOURCE_MARKER, 1)[1]
        calls.append((prior, text))
        if malformed:
            return "{malformed"
        block = {"text": text, "visibility": "shared", "known_by": []}
        if text.startswith("Mira secretly"):
            block.update(visibility="restricted", known_by=["Mira"])
        return json.dumps({"blocks": [*prior.get("blocks", []), block]})

    def run():
        db.execute("UPDATE memory_jobs SET next_attempt_at=0 WHERE layer='summary'")
        db.commit()
        return memory.generate_session_summary_result(
            db,
            "c",
            SESSION,
            force=True,
            durable=True,
            max_segments=1,
            provider_port=ProviderPort(generate),
            app_settings=settings,
        )

    first = run()
    assert first.covered_until_rowid == 1 and not first.complete
    second = run()
    assert second.complete and second.covered_until_rowid == 2
    accepted = load_artifact_classification(db, "c", "s", "summary")[2]
    assert accepted == [
        {"text": "Tower reached.", "visibility": "shared", "known_by": []},
        {"text": "Mira secretly hides the key.", "visibility": "restricted", "known_by": ["mira"]},
    ]
    third = append(db, "Old ending.")
    malformed = True
    failed = run()
    assert not failed.complete and failed.covered_until_rowid == 2
    assert load_artifact_classification(db, "c", "s", "summary")[2] == accepted
    assert db.execute("SELECT count(*) FROM memory_segments WHERE layer='summary' AND valid=1").fetchone() == (2,)
    malformed = False
    assert run().complete
    assert calls[-1] == ({"blocks": accepted}, "Old ending.")
    db.execute("UPDATE messages SET content='Replacement ending.' WHERE id=?", (third,))
    db.commit()
    rewritten = run()
    assert rewritten.complete and rewritten.covered_until_rowid == third
    assert calls[-1] == ({"blocks": accepted}, "Replacement ending.")
    assert [text for _, text in calls] == [
        "Tower reached.",
        "Mira secretly hides the key.",
        "Old ending.",
        "Old ending.",  # One bounded format regeneration of the same canonical source.
        "Old ending.",
        "Replacement ending.",
    ]
    assert load_artifact_classification(db, "c", "s", "summary")[2] == [
        *accepted,
        {"text": "Replacement ending.", "visibility": "shared", "known_by": []},
    ]


@pytest.mark.parametrize("mode", ["off", "shadow", "enabled"])
def test_summary_pending_source_parts_remain_private_until_complete(db, tmp_path, monkeypatch, mode):
    # Dropping a pending source part or publishing its incomplete accumulator is a bug.
    configure(monkeypatch, mode)
    settings = make_test_settings(home=tmp_path)
    text = "HEAD" + "x" * 13992 + "TAIL"
    rowid = append(db, text)
    supplied = []

    def generate(api_key, model, messages, **kwargs):
        prior, remainder = unpack(messages)
        part = remainder.split(SOURCE_MARKER, 1)[1]
        supplied.append(part)
        blocks = prior.get("blocks", [])
        for marker in ("HEAD", "TAIL"):
            if marker in part:
                blocks.append({"text": marker, "visibility": "restricted", "known_by": ["Mira"]})
        return json.dumps({"blocks": blocks})

    def run():
        return memory.generate_session_summary_result(
            db,
            "c",
            SESSION,
            force=True,
            durable=True,
            max_segments=1,
            provider_port=ProviderPort(generate),
            app_settings=settings,
        )

    first = run()
    assert not first.complete and first.covered_until_rowid == 0
    assert memory.get_session_summary(db, "c", "s") == ("", 0)
    assert db.execute("SELECT count(*) FROM memory_layer_checkpoints WHERE layer='summary'").fetchone() == (0,)
    second = run()
    assert second.complete and second.covered_until_rowid == rowid
    assert supplied == [text[:12000], text[12000:]]
    assert memory.get_session_summary(db, "c", "s") == ("HEAD\nTAIL", rowid)
    assert load_artifact_classification(db, "c", "s", "summary")[2] == [
        {"text": "HEAD", "visibility": "restricted", "known_by": ["mira"]},
        {"text": "TAIL", "visibility": "restricted", "known_by": ["mira"]},
    ]


def test_summary_near_capacity_instructs_bounded_compaction_on_both_calls(db, tmp_path):
    """Near-full checkpoints must request a compact complete result, including repair."""
    append(db, "Later, a promise must be honored even when the earlier summary is full.")
    source = next_source_segment(db, "c", "s", "summary")
    settings = make_test_settings(home=tmp_path)
    private = {"text": "Mira promised not to reveal the key.", "visibility": "restricted", "known_by": ["Mira"]}
    old = {
        "blocks": [
            {"text": ("Old public fact number %02d. " % i) * 16, "visibility": "shared", "known_by": []}
            for i in range(27)
        ]
        + [private]
    }
    assert 10000 < sum(len(b["text"]) for b in old["blocks"]) < 12000
    too_long = {
        "blocks": [
            {"text": "A" * 4200, "visibility": "shared", "known_by": []},
            {"text": "B" * 4200, "visibility": "shared", "known_by": []},
            {"text": "C" * 4200, "visibility": "restricted", "known_by": ["Mira"]},
        ]
    }
    # A model-supplied repaired result must still carry forward earlier
    # continuity; we do not test acceptance of an artifact that drops it.
    accepted = {
        "blocks": [
            *old["blocks"],
            {"text": "The promise remains active.", "visibility": "shared", "known_by": []},
        ]
    }
    requests = []

    def generate(api_key, model, messages, **kwargs):
        requests.append(copy.deepcopy(messages))
        return json.dumps(too_long if len(requests) == 1 else accepted)

    result = memory.extract_summary_segment(
        db,
        "c",
        SESSION,
        old,
        source,
        provider_port=ProviderPort(generate),
        app_settings=settings,
    )
    assert result["blocks"][:-1] == [
        *[{**block, "text": block["text"].strip()} for block in old["blocks"][:-1]],
        {"text": private["text"], "visibility": "restricted", "known_by": ["mira"]},
    ]
    assert result["blocks"][-1] == accepted["blocks"][-1]
    assert len(requests) == 2
    for messages in requests:
        combined_system = " ".join(item["content"] for item in messages if item["role"] == "system")
        assert "12,000 characters" in combined_system
        assert "10,800 characters" in combined_system
        assert "visibility" in combined_system and "known_by" in combined_system
        prior, continuation = unpack(messages)
        assert prior == old
        assert continuation.endswith(SOURCE_MARKER + source.content)
        assert "A" * 4200 not in combined_system


def test_summary_near_capacity_rejects_second_oversized_response_without_publication(db, tmp_path):
    append(db, "New source part remains available after malformed extraction.")
    source = next_source_segment(db, "c", "s", "summary")
    settings = make_test_settings(home=tmp_path)
    long_response = json.dumps(
        {"blocks": [{"text": letter * 4200, "visibility": "shared", "known_by": []} for letter in ("A", "B", "C")]}
    )
    calls = []

    def generate(api_key, model, messages, **kwargs):
        calls.append(messages)
        return long_response

    with pytest.raises(ValueError, match="bounded, nonempty"):
        memory.extract_summary_segment(
            db,
            "c",
            SESSION,
            {"blocks": [{"text": "Prior fact", "visibility": "shared", "known_by": []}]},
            source,
            provider_port=ProviderPort(generate),
            app_settings=settings,
        )
    assert len(calls) == 2
    assert memory.get_session_summary(db, "c", "s") == ("", 0)
    assert next_source_segment(db, "c", "s", "summary") == source


def test_summary_compaction_must_never_repair_conflicting_audience(db, tmp_path):
    append(db, "Another source part.")
    source = next_source_segment(db, "c", "s", "summary")
    settings = make_test_settings(home=tmp_path)
    attempts = []

    def generate(api_key, model, messages, **kwargs):
        attempts.append(messages)
        return json.dumps(
            {"blocks": [{"text": "Alice secretly knows the password", "visibility": "shared", "known_by": ["Alice"]}]}
        )

    with pytest.raises(ValueError):
        memory.extract_summary_segment(
            db,
            "c",
            SESSION,
            {"blocks": []},
            source,
            provider_port=ProviderPort(generate),
            app_settings=settings,
        )
    assert len(attempts) == 1
    assert memory.get_session_summary(db, "c", "s") == ("", 0)


def test_small_accepted_summary_uses_short_length_contract(db, tmp_path):
    """Do not add a near-capacity compaction prompt to normal helper requests."""
    append(db, "A simple canonical turn.")
    source = next_source_segment(db, "c", "s", "summary")
    settings = make_test_settings(home=tmp_path)
    instructions = []

    def generate(api_key, model, messages, **kwargs):
        instructions.append(" ".join(item["content"] for item in messages if item["role"] == "system"))
        return json.dumps({"blocks": [{"text": "A simple fact.", "visibility": "shared", "known_by": []}]})

    result = memory.extract_summary_segment(
        db,
        "c",
        SESSION,
        {"blocks": []},
        source,
        provider_port=ProviderPort(generate),
        app_settings=settings,
    )
    assert len(result["blocks"]) == 1
    assert len(instructions) == 1
    assert "12,000 characters" in instructions[0]
    assert "10,800 characters" not in instructions[0]
