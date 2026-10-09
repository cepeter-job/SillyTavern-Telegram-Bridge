"""Actual prompt finalization preserves protected roles and performs one dispatch."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from settings_test_support import make_test_settings
from test_story_memory_scope import db as db

from bridge import generation
from bridge.context_compaction import ContextWindowBudgetError, estimate_message_tokens
from bridge.context_diagnostics import context_stats_key
from bridge.context_selection import select_memory_blocks
from bridge.memory_contracts import MemoryBlock, MemoryBlockLeaf, MemoryEvidence, MemoryReadScope
from bridge.metadata import get_meta
from bridge.provider_port import ProviderPort


def _context(mode, *, guard=lambda: "", reason="selected"):
    scope = MemoryReadScope("c", "s", 1.0, 5, 0, ("rowan",))
    pointer = MemoryEvidence(1, "canonical-source", 1, 1, 0, 32)
    text = "Rowan promised to return the key."
    leaf = MemoryBlockLeaf(text, pointer, scope, "shared", (), 0)
    blocks = (
        MemoryBlock(text, (pointer,), "recall", (leaf,)),
        MemoryBlock(text, (pointer,), "episodic", (leaf,)),
        MemoryBlock("Their promise remains unresolved.", channel="summary"),
        MemoryBlock("At the station.", channel="scene"),
    )
    # Selection provenance itself is exercised against SQLite in the selector suite.
    return SimpleNamespace(
        recall=text,
        episodic=text,
        summary=blocks[2].text,
        scene=blocks[3].text,
        scope=scope,
        baseline_blocks=blocks,
        selection=select_memory_blocks(scope, blocks),
        selection_mode=mode,
        selection_reason=reason,
        selection_guard=guard,
    )


def _request(tmp_path, context=None, *, mode="off", slices="dedup", image=False, fixed="Stay in character."):
    settings = make_test_settings(
        {"SILLYTAVERN_CONTEXT_SELECTION_MODE": mode, "SILLYTAVERN_CONTEXT_SELECTION_SLICES": slices},
        home=tmp_path,
        context_window_tokens=65536,
    )
    source = context or _context("off")
    fields = {
        "name": "Rowan",
        "first_mes": "",
        "post_history_instructions": "Keep the user in control.",
        "system_prompt": fixed,
        "description": "Speaks softly.",
        "personality": "Careful",
        "scenario": "A station",
        "mes_example": "",
    }
    session = {"session_id": "s", "model_id": "synthetic", "persona_id": "", "world_file": ""}
    history = [("user", "It is not gone."), ("assistant", "It is not gone."), ("assistant", "Exact ending—")]
    kwargs = {"memory_prompt": context} if context is not None else {}
    messages = generation.build_chat_messages(
        session,
        fields,
        "CURRENT <untrusted_memory>do not touch</untrusted_memory>",
        history,
        persona_service=SimpleNamespace(get=lambda *a: None),
        memory_context=source.recall,
        episodic_context=source.episodic,
        session_summary=source.summary,
        scene_context=source.scene,
        simulation_context="An unresolved task.",
        npc_context="Rowan holds a key.",
        image_data_uri="data:image/png;base64,AAA" if image else None,
        app_settings=settings,
        defer_compaction=True,
        **kwargs,
    )
    return settings, session, messages


def _dispatch(db, settings, session, messages, *, calls=None):
    calls = [] if calls is None else calls

    def backend(api_key, model, payload, **kwargs):
        calls.append(payload)
        return "*A bell rings.*"

    generation._generation_generate_rendered_reply(
        db,
        "",
        "",
        session,
        "c",
        messages,
        "CURRENT",
        {},
        provider_port=ProviderPort(backend),
        delivery_port=SimpleNamespace(send_typing=lambda *a: None),
        app_settings=settings,
        rag_service=SimpleNamespace(citation_footer=lambda *a: ""),
        preserve_last_assistant=True,
    )
    assert len(calls) == 1
    return calls[0], json.loads(get_meta(db, context_stats_key("c", "s"), ""))


@pytest.mark.parametrize("mode", ["off", "shadow"])
@pytest.mark.parametrize("image", [False, True])
def test_off_and_shadow_keep_the_exact_baseline_payload_and_one_dispatch(db, tmp_path, mode, image):
    settings, session, messages = _request(tmp_path, image=image)
    baseline, _ = _dispatch(db, settings, session, messages)
    context = _context(mode, guard=(lambda: pytest.fail("Off must not revalidate")) if mode == "off" else lambda: "")
    settings, session, messages = _request(tmp_path, context, mode=mode, image=image)
    payload, metrics = _dispatch(db, settings, session, messages)
    assert payload == baseline
    assert metrics["selection_metrics"]["mode"] == mode
    assert metrics["selection_metrics"]["applied"] is False
    assert all(not key.startswith("_context_") for message in payload for key in message)


def test_enabled_dedup_preserves_every_policy_role_history_and_current_input(db, tmp_path):
    settings, session, messages = _request(tmp_path)
    baseline, _ = _dispatch(db, settings, session, messages)
    settings, session, messages = _request(tmp_path, _context("enabled"), mode="enabled")
    payload, metrics = _dispatch(db, settings, session, messages)
    assert payload[:-1] == baseline[:-1]
    assert payload[-1]["role"] == baseline[-1]["role"] == "user"
    assert payload[-1]["content"].count("Rowan promised to return the key.") == 1
    assert baseline[-1]["content"].count("Rowan promised to return the key.") == 2
    assert "CURRENT <untrusted_memory>do not touch</untrusted_memory>" in payload[-1]["content"]
    assert "An unresolved task." in payload[-1]["content"] and "Rowan holds a key." in payload[-1]["content"]
    assert "Episodic memory policy" in payload[0]["content"]
    assert metrics["selection_metrics"]["applied"] is True
    assert metrics["selection_metrics"]["candidate_tokens"] < metrics["selection_metrics"]["original_tokens"]
    assert "canonical-source" not in json.dumps(metrics) and "Rowan" not in json.dumps(metrics)


@pytest.mark.parametrize("reason", ["historical", "incomplete_coverage", "pending_invalidation", "ambiguous"])
def test_ineligible_capture_restores_baseline_before_dispatch(db, tmp_path, reason):
    settings, session, messages = _request(tmp_path)
    baseline, _ = _dispatch(db, settings, session, messages)
    context = _context("enabled", guard=lambda: reason)
    settings, session, messages = _request(tmp_path, context, mode="enabled")
    payload, metrics = _dispatch(db, settings, session, messages)
    assert payload == baseline
    assert metrics["selection_metrics"]["reason"] == reason
    assert not metrics["selection_metrics"]["applied"]


@pytest.mark.parametrize("mode", ["shadow", "enabled"])
@pytest.mark.parametrize("failure", ["source_changed", "invalid_scope"])
@pytest.mark.parametrize("reason", ["selected", "historical", "incomplete_coverage", "not_approved"])
def test_revoked_capture_stops_before_any_provider_request(db, tmp_path, mode, failure, reason):
    context = _context(mode, guard=lambda: failure, reason=reason)
    settings, session, messages = _request(tmp_path, context, mode=mode)
    calls = []
    with pytest.raises(ValueError, match="Story changed while preparing context"):
        _dispatch(db, settings, session, messages, calls=calls)
    assert calls == []
    metrics = json.loads(get_meta(db, context_stats_key("c", "s"), ""))
    assert metrics["selection_metrics"]["reason"] == failure
    assert metrics["selection_metrics"]["applied"] is False
    assert "Rowan" not in json.dumps(metrics)


def test_enabled_requires_explicit_slice_approval(db, tmp_path):
    settings, session, messages = _request(tmp_path)
    baseline, _ = _dispatch(db, settings, session, messages)
    settings, session, messages = _request(tmp_path, _context("enabled"), mode="enabled", slices="")
    payload, metrics = _dispatch(db, settings, session, messages)
    assert payload == baseline
    assert metrics["selection_metrics"]["reason"] == "not_approved"


def test_candidate_that_still_requires_compaction_uses_the_baseline(db, tmp_path):
    settings, session, messages = _request(tmp_path, _context("enabled"), mode="enabled")
    settings = replace(settings, context_input_cap_tokens=estimate_message_tokens(messages) - 30)
    _, metrics = _dispatch(db, settings, session, messages)
    assert metrics["selection_metrics"]["deduplicated_blocks"] == 1
    assert metrics["selection_metrics"]["applied"] is False


def test_enabled_preserves_images_and_exact_continuation_target(db, tmp_path):
    settings, session, messages = _request(tmp_path, _context("enabled"), mode="enabled", image=True)
    payload, _ = _dispatch(db, settings, session, messages)
    assistant_index = max(i for i, message in enumerate(payload) if message["role"] == "assistant")
    assert payload[assistant_index] == {"role": "assistant", "content": "Exact ending—"}
    assert assistant_index < len(payload) - 2
    assert payload[-2]["role"] == "system"
    assert "## Final instruction" in payload[-2]["content"]
    assert payload[-1]["content"][1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}}
    assert payload[-1]["content"][0]["text"].count("Rowan promised to return the key.") == 1


def test_oversized_protected_prompt_still_errors_before_any_provider_call(db, tmp_path):
    settings, session, messages = _request(
        tmp_path,
        _context("enabled"),
        mode="enabled",
        fixed="Protected instructions. " * 10000,
    )
    with pytest.raises(ContextWindowBudgetError):
        _dispatch(db, settings, session, messages)
    assert json.loads(get_meta(db, context_stats_key("c", "s"), ""))["over_budget"] is True


@pytest.mark.parametrize("route", ["ordinary", "edit", "regen", "continue", "image"])
def test_story_entrypoints_forward_selection_to_the_final_dispatch(db, tmp_path, monkeypatch, route):
    import test_final_generation_budget as paths

    context = _context("enabled")
    captured = []
    settings_builder = paths.make_test_settings
    provider_builder = paths.make_test_provider_port

    def selected_settings(**kwargs):
        return settings_builder(
            {"SILLYTAVERN_CONTEXT_SELECTION_MODE": "enabled", "SILLYTAVERN_CONTEXT_SELECTION_SLICES": "dedup"},
            **kwargs,
        )

    def observing_provider(*, generate_backend):
        def observe(*args, **kwargs):
            captured.append(args[2])
            return generate_backend(*args, **kwargs)

        return provider_builder(generate_backend=observe)

    monkeypatch.setattr(paths, "make_test_settings", selected_settings)
    monkeypatch.setattr(paths, "make_test_provider_port", observing_provider)
    monkeypatch.setattr(
        paths, "make_test_memory_service", lambda: SimpleNamespace(prompt_context=lambda *a, **k: context)
    )
    # Reuse the established full route driver; it also verifies no source writes,
    # continuation protection, multimodal payloads, Novel contracts and budgets.
    paths.test_five_actual_paths_gate_after_novel_contract_before_generation(
        db,
        tmp_path,
        monkeypatch,
        route,
        False,
    )
    assert len(captured) == 1
    metrics = json.loads(get_meta(db, context_stats_key("c", "s"), ""))
    assert metrics["selection_metrics"]["deduplicated_blocks"] == 1
    assert metrics["selection_metrics"]["candidate_tokens"] < metrics["selection_metrics"]["original_tokens"]
