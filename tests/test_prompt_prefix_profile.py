"""A read-only profiler must not confuse structural stability with savings."""

import copy
import importlib.util
import json

import pytest


def profiler(**kwargs):
    assert importlib.util.find_spec("bridge.prompt_prefix_profile"), "prefix profiler not implemented"
    from bridge.prompt_prefix_profile import PrefixProfiler

    return PrefixProfiler(**kwargs)


def scope(**changes):
    return dict(
        session="chat/session/incarnation",
        character="card-digest",
        reader="reader-policy-digest",
        branch="branch/revision",
        provider="nanogpt-account-route",
        model="model-id",
        stage="builder",
        **changes,
    )


def request(content="A" * 128 + "changing summary", *, suffix="question", metadata=None):
    return {
        "messages": [
            {"role": "system", "content": content, **(metadata or {})},
            {"role": "user", "content": suffix},
        ],
        "temperature": 0.8,
    }


def test_partial_prefix_inside_mixed_system_message_is_not_whole_message_stability():
    p = profiler()
    first, second = request(), request("A" * 128 + "different summary", suffix="next question")
    original = copy.deepcopy(first)
    p.observe(first, scope())
    p.observe(second, scope())
    result = p.report()
    group = result["cohorts"][0]
    assert first == original
    assert group["stable_prefix_messages"] == 0
    assert group["stable_prefix_characters"] == 128
    assert group["first_varying_instruction_position"] == 0
    assert result["prompt_mutations"] == 0
    assert result["optimization_authorized"] is False
    assert result["provider_savings_fraction"] is None
    assert result["cache_hit_rate_prediction"] is None


def test_single_observation_never_claims_cross_request_stability():
    p = profiler()
    p.observe(request(), scope())
    group = p.report()["cohorts"][0]
    assert group["comparison_status"] == "insufficient_samples"
    assert group["stable_prefix_characters"] == 0


def test_stable_instruction_after_changed_message_does_not_extend_prefix():
    p = profiler()
    for variable in ("one", "two"):
        r = request(variable)
        r["messages"].insert(1, {"role": "system", "content": "FIXED LATER RULE"})
        p.observe(r, scope())
    group = p.report()["cohorts"][0]
    assert group["stable_prefix_characters"] == 0
    assert group["positions"][1]["stable"] is True
    assert group["positions"][1]["blocked_by_earlier_variation"] is True


@pytest.mark.parametrize("field", ["session", "character", "reader", "branch", "provider", "model", "stage"])
def test_different_contexts_are_never_pooled(field):
    p = profiler()
    a, b = scope(), scope()
    b[field] = "provider" if field == "stage" else "other-context"
    p.observe(request(), a)
    p.observe(request(), b)
    groups = p.report()["cohorts"]
    assert len(groups) == 2
    assert all(g["comparison_status"] == "insufficient_samples" for g in groups)


def test_role_metadata_and_request_envelope_changes_invalidate_prefix_claim():
    for change in ("role", "name", "tools"):
        p = profiler()
        a, b = request(), request()
        if change == "role":
            b["messages"][0]["role"] = "developer"
        elif change == "name":
            b["messages"][0]["name"] = "different author"
        else:
            b["tools"] = [{"name": "different tool"}]
        p.observe(a, scope())
        p.observe(b, scope())
        assert p.report()["cohorts"][0]["stable_prefix_characters"] == 0


def test_repeated_dialogue_is_not_an_instruction_duplicate_or_deletion_permission():
    p = profiler()
    r = request()
    r["messages"] += [
        {"role": "assistant", "content": "I refuse."},
        {"role": "user", "content": "I refuse."},
        {"role": "system", "content": "Format only."},
        {"role": "system", "content": "Format only."},
        {"role": "developer", "content": "Format only."},
        {"role": "system", "content": "Format only.", "name": "other"},
    ]
    p.observe(r, scope())
    duplicates = p.report()["cohorts"][0]["latest_instruction_duplicates"]
    assert len(duplicates) == 1
    assert duplicates[0]["positions"] == [4, 5]
    assert duplicates[0]["removal_authorized"] is False


def test_native_optional_spans_reveal_changing_summary_without_parsing_story_headings():
    p = profiler()
    for summary in ("first", "later"):
        r = request("STATIC" + summary)
        r["messages"][0]["_context_optional"] = [{"kind": "summary", "start": 6, "end": 11}]
        p.observe(r, scope())
    sections = p.report()["cohorts"][0]["sections"]
    assert next(s for s in sections if s["kind"] == "summary")["stable"] is False
    assert next(s for s in sections if s["kind"] == "instruction_remainder")["stable"] is True
    assert p.report()["cohorts"][0]["stable_prefix_characters"] == 0  # conservative 128-character blocks


def test_recent_window_and_cohort_limit_bound_retained_state():
    p = profiler(window=2, max_cohorts=1)
    for index in range(5):
        p.observe(request(str(index)), scope())
    group = p.report()["cohorts"][0]
    assert group["observations_seen"] == 5
    assert group["retained_samples"] == 2
    assert group["evicted_samples"] == 3
    other = scope()
    other["session"] = "different"
    with pytest.raises(ValueError, match="cohort_limit"):
        p.observe(request(), other)
    assert p.report()["cohorts"][0]["observations_seen"] == 5


def test_no_text_raw_identity_or_predictable_hash_is_retained_or_exported():
    p = profiler()
    r = request("PRIVATE_CANARY: a secret plan", suffix="PRIVATE_QUESTION")
    p.observe(r, scope())
    public = json.dumps(p.report()) + repr(p)
    assert "PRIVATE_CANARY" not in public
    assert "PRIVATE_QUESTION" not in public
    assert "chat/session/incarnation" not in public
    other = profiler()
    other.observe(r, scope())
    assert p.report()["cohorts"][0]["scope_fingerprint"] != other.report()["cohorts"][0]["scope_fingerprint"]


def test_provider_usage_unknown_is_not_zero_and_no_savings_or_accepted_work_claim():
    p = profiler()
    s = scope()
    s["stage"] = "provider"
    p.observe(request(), s, usage={"input_tokens": 100, "cached_tokens": 80, "output_tokens": 10})
    p.observe(request(), s)
    usage = p.report()["cohorts"][0]["provider_usage"]
    assert usage["input_samples"] == 1
    assert usage["unknown_input_samples"] == 1
    assert usage["input_tokens_sum"] == 100
    assert usage["cached_tokens_sum"] == 80
    assert usage["complete_window_input_tokens"] is None
    assert p.report()["accepted_work_savings_fraction"] is None


@pytest.mark.parametrize("bad", [True, -1, float("nan"), float("inf"), "PRIVATE_ERROR"])
def test_invalid_provider_usage_is_rejected_without_echo(bad):
    p = profiler()
    with pytest.raises(ValueError, match="invalid_usage") as error:
        p.observe(request(), scope(), usage={"input_tokens": bad})
    assert "PRIVATE_ERROR" not in str(error.value)
    assert not p.report()["cohorts"]


def test_invalid_spans_do_not_mutate_profiler_or_leak_payload():
    p = profiler()
    r = request()
    r["messages"][0]["_context_optional"] = [{"kind": "PRIVATE_SPAN", "start": 0, "end": 50000}]
    with pytest.raises(ValueError, match="invalid_sections") as error:
        p.observe(r, scope())
    assert "PRIVATE_SPAN" not in str(error.value)
    assert not p.report()["cohorts"]


def test_multimodal_is_hashed_but_not_claimed_as_complete_token_estimate():
    p = profiler()
    r = request()
    r["messages"][1]["content"] = [
        {"type": "text", "text": "question"},
        {"type": "image_url", "image_url": {"url": "PRIVATE_IMAGE"}},
    ]
    p.observe(r, scope())
    p.observe(r, scope())
    group = p.report()["cohorts"][0]
    assert group["non_text_payload_present"] is True
    assert group["full_prompt_token_count_known"] is False
    assert "PRIVATE_IMAGE" not in json.dumps(p.report())


def test_global_fingerprint_budget_rejects_without_evicting_previous_samples(monkeypatch):
    from bridge import prompt_prefix_profile as module

    p = profiler()
    p.observe(request("short"), scope())
    before = p.report()
    assert "retained_fingerprint_units" in before, "fingerprint state budget missing"
    monkeypatch.setattr(module, "MAX_FINGERPRINT_UNITS", before["retained_fingerprint_units"])
    with pytest.raises(ValueError, match="fingerprint_limit"):
        p.observe(request("A" * 5000), scope())
    assert p.report()["cohorts"] == before["cohorts"]


def test_optional_section_stamp_count_is_bounded_before_state_retention(monkeypatch):
    from bridge import prompt_profile_snapshot as module

    monkeypatch.setattr(module, "MAX_SECTIONS", 2, raising=False)
    r = request("abcdef")
    r["messages"][0]["_context_optional"] = [
        {"kind": "summary", "start": 0, "end": 1},
        {"kind": "summary", "start": 2, "end": 3},
        {"kind": "summary", "start": 4, "end": 5},
    ]
    p = profiler()
    with pytest.raises(ValueError, match="section_limit"):
        p.observe(r, scope())
    assert not p.report()["cohorts"]


def test_window_changes_recompute_prefix_and_preserve_nonmutation_and_whitespace():
    p = profiler(window=2)
    original = request("A" * 128 + "  \n\n")
    untouched = copy.deepcopy(original)
    p.observe(request("different"), scope())
    p.observe(original, scope())
    assert p.report()["cohorts"][0]["stable_prefix_characters"] == 0
    p.observe(original, scope())
    group = p.report()["cohorts"][0]
    assert group["stable_prefix_messages"] == 1
    assert group["stable_prefix_characters"] == 132
    assert original == untouched


def test_missing_instruction_and_unicode_partial_prefix_do_not_inflate_stability():
    p = profiler()
    for tail in ("one", "two"):
        p.observe(request("語" * 128 + tail), scope())
    assert p.report()["cohorts"][0]["stable_prefix_characters"] == 128
    p.observe({"messages": [{"role": "user", "content": "question"}]}, scope())
    assert p.report()["cohorts"][0]["stable_prefix_characters"] == 0


def test_subsequent_mutation_of_caller_input_cannot_change_retained_observation():
    p = profiler()
    r, s = request(), scope()
    p.observe(r, s)
    before = p.report()
    r["messages"][0]["content"] = "REPLACED_SECRET"
    s["reader"] = "OTHER_READER"
    assert p.report() == before
    assert "REPLACED_SECRET" not in repr(p._cohorts)


@pytest.mark.parametrize("options", [{"window": True}, {"window": 33}, {"max_cohorts": 17}, {"chars_per_token": 0}])
def test_invalid_profiler_limits_rejected(options):
    with pytest.raises(ValueError):
        profiler(**options)


def test_native_demo_does_not_use_network(tmp_path, monkeypatch):
    import socket

    from tools.prompt_prefix_demo import demo_records

    def prohibited(*args, **kwargs):
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket, "create_connection", prohibited)
    p = profiler()
    for item in demo_records(tmp_path):
        p.observe(item["request"], item["scope"])
    assert len(p.report()["cohorts"]) == 3
