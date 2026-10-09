"""Opt-in prose profiles use the native catalogue, never a second state engine."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from settings_test_support import make_test_settings

from bridge.card_content import build_system_prompt, card_fields, load_system_prompts
from bridge.context_compaction import compact_chat_messages
from bridge.generation import build_chat_messages
from bridge.narrative_policy import narrative_policy, story_policy_text
from bridge.narrative_values import NarrativeSettings

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "config" / "system_prompts.example"
PROFILES = ("writer_continuity", "writer_dialogue", "writer_ensemble", "writer_magical_realism")
POVS = ("first_person", "third_person_user", "third_person_rotating", "omniscient", "cinematic")


def catalogue(tmp_path):
    settings = make_test_settings(home=tmp_path, system_prompts_dir=EXAMPLES)
    return settings, load_system_prompts(app_settings=settings)


@pytest.mark.parametrize("profile", PROFILES)
def test_profile_is_bounded_native_json_with_explicit_native_ownership(tmp_path, profile):
    _, prompts = catalogue(tmp_path)
    assert profile in prompts
    item = prompts[profile]
    assert item["name"].startswith("Native ")
    assert len(item["prompt"]) <= 2400
    assert "Native session policies remain authoritative" in item["prompt"]
    assert "user's decisions and private thoughts" in item["prompt"]
    assert "character knowledge" in item["prompt"]
    assert "trackers and dice" in item["prompt"]
    assert "{{" not in item["prompt"]
    assert "[CHOICES]" not in item["prompt"]
    assert "<script" not in item["prompt"]


def assemble(settings, profile_text, *, pov="cinematic", language="auto", history=()):
    fields = card_fields(
        {"name": "Rowan", "description": "A station keeper.", "first_mes": "Opening scene."}, app_settings=settings
    )
    session = {
        "persona_id": "",
        "world_file": "",
        "model_id": "synthetic",
        "system_prompt": profile_text,
        "response_language": language,
    }
    focus = "ensemble" if pov == "first_person" else "user"
    policy = story_policy_text(narrative_policy(NarrativeSettings(pov_mode=pov, scene_focus=focus)))
    return build_chat_messages(
        session,
        fields,
        "I ask whether the train has left.",
        list(history),
        persona_service=SimpleNamespace(),
        narrative_context=policy,
        app_settings=settings,
        defer_compaction=True,
    )


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("pov", POVS)
@pytest.mark.parametrize("language", ["auto", "id"])
def test_selected_profile_and_native_policies_are_assembled_once(tmp_path, profile, pov, language):
    settings, prompts = catalogue(tmp_path)
    assert profile in prompts
    text = prompts[profile]["prompt"]
    messages = assemble(settings, text, pov=pov, language=language, history=(("assistant", "Earlier scene."),))
    joined = "\n".join(str(m["content"]) for m in messages)
    assert joined.count(text) == 1
    for marker in ("## Narrative Policy", "## Telegram Roleplay Output Contract", "## Canonical story state"):
        assert joined.count(marker) == 1
    assert "Never invent the user's dialogue" in joined
    assert "Private agendas, offscreen facts" in joined
    assert messages[-1]["role"] == "user"
    assert "I ask whether the train has left." in messages[-1]["content"]


@pytest.mark.parametrize("profile", PROFILES)
def test_profile_off_restores_baseline_without_mutating_catalogue(tmp_path, profile):
    settings, prompts = catalogue(tmp_path)
    assert profile in prompts
    snapshot = json.dumps(prompts, sort_keys=True)
    baseline = assemble(settings, "")
    selected = assemble(settings, prompts[profile]["prompt"])
    assert selected != baseline
    assert assemble(settings, "") == baseline
    assert json.dumps(prompts, sort_keys=True) == snapshot
    assert "## Session System Prompt" not in baseline[0]["content"]


@pytest.mark.parametrize("profile", PROFILES)
def test_long_history_compaction_keeps_selected_profile_and_current_request(tmp_path, profile):
    settings, prompts = catalogue(tmp_path)
    assert profile in prompts
    text = prompts[profile]["prompt"]
    messages = assemble(settings, text, history=(("assistant", "Old station scene. " * 2000),) * 8)
    current = messages[-1]["content"]
    compacted, stats = compact_chat_messages(messages, 1800, min_recent_messages=0, app_settings=settings)
    assert stats["dropped_history"] > 0
    assert not stats["over_budget"]
    assert text in compacted[0]["content"]
    assert compacted[-1]["content"] == current


def test_existing_example_profiles_remain_available(tmp_path):
    _, prompts = catalogue(tmp_path)
    assert {"natural", "balanced", "concise"} <= prompts.keys()


@pytest.mark.parametrize("profile", ("off", *PROFILES))
@pytest.mark.parametrize("case_index", range(8))
def test_synthetic_cases_have_an_exact_profile_only_request_delta(tmp_path, profile, case_index):
    data = json.loads((ROOT / "tests/fixtures/writing_profile_workload.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    cases = data["cases"]
    assert len(cases) == len({case["id"] for case in cases}) == 8
    case = cases[case_index]
    assert case["required_facts"] and case["forbidden_inferences"] and case["review_focus"]
    settings, prompts = catalogue(tmp_path)
    fields = card_fields(case["card"], app_settings=settings)
    policy = story_policy_text(narrative_policy(NarrativeSettings(**case["narrative_settings"])))
    history = [(item["role"], item["content"]) for item in case["history"]]
    assert all(role in {"user", "assistant"} for role, _ in history)

    def request(text):
        session = {
            "persona_id": "",
            "world_file": "",
            "model_id": "synthetic",
            "system_prompt": text,
            "response_language": case["response_language"],
        }
        messages = build_chat_messages(
            session,
            fields,
            case["user_text"],
            history,
            persona_service=SimpleNamespace(),
            narrative_context=policy,
            app_settings=settings,
            defer_compaction=True,
        )
        return [
            {key: value for key, value in message.items() if not key.startswith("_context_")} for message in messages
        ]

    text = "" if profile == "off" else prompts[profile]["prompt"]
    baseline = request("")
    selected = request(text)
    expected = [{**message} for message in baseline]
    if text:
        prefix = build_system_prompt(fields, settings.default_user_name, app_settings=settings)
        assert baseline[0]["content"].startswith(prefix)
        expected[0]["content"] = (
            prefix + "\n\n## Session System Prompt\n" + text + baseline[0]["content"][len(prefix) :]
        )
    assert selected == expected
    assert request(text) == selected
    assert case["user_text"] in selected[-1]["content"]
    assert all(fact in "\n".join(m["content"] for m in selected) for fact in case["required_facts"])
