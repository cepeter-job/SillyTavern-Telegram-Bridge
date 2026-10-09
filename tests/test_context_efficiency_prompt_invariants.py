"""Offline replay checks every system message, not an obsolete array offset."""

import copy
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from settings_test_support import make_test_settings

from bridge.generation import build_chat_messages
from bridge.light_novel_format import add_inline_contract

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def replay_case(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    replay = importlib.import_module("context_efficiency_replay")
    fixture = json.loads(replay.FIXTURE.read_text(encoding="utf-8"))
    capture = fixture["cases"][2]
    settings = make_test_settings(home=tmp_path)
    session = dict(fixture["session"], model_id="synthetic")
    messages = build_chat_messages(
        session,
        fixture["fields"],
        capture["user_text"],
        capture["history"],
        persona_service=SimpleNamespace(),
        app_settings=settings,
        defer_compaction=True,
        **fixture["contexts"],
    )
    return replay, add_inline_contract(messages, 4, session["response_language"]), capture, fixture


def test_replay_accepts_protected_instructions_in_late_system_message(replay_case):
    replay, messages, capture, fixture = replay_case
    result = replay.check_invariants(messages, capture, fixture)
    assert result["satisfied"]
    assert result["post_history_placement_preserved"]
    assert result["native_policy_precedence_preserved"]


@pytest.mark.parametrize("change", ["early", "duplicate", "user_role", "after_user"])
def test_replay_rejects_misplaced_or_duplicated_post_history(replay_case, change):
    replay, messages, capture, fixture = replay_case
    messages = copy.deepcopy(messages)
    index = next(i for i, message in enumerate(messages) if "## Final instruction" in message["content"])
    if change == "early":
        messages.insert(0, messages.pop(index))
    elif change == "duplicate":
        messages.insert(index, copy.deepcopy(messages[index]))
    elif change == "user_role":
        messages[index]["role"] = "user"
    else:
        messages.append(messages.pop(index))
    assert not replay.check_invariants(messages, capture, fixture)["satisfied"]


@pytest.mark.parametrize("marker", ["## Canonical story state", "## Telegram Roleplay Output Contract"])
def test_replay_rejects_policy_moved_to_lower_priority_user_content(replay_case, marker):
    replay, messages, capture, fixture = replay_case
    messages = copy.deepcopy(messages)
    index = next(i for i, message in enumerate(messages) if marker in message["content"])
    messages[index]["content"] = messages[index]["content"].replace(marker, "REMOVED POLICY")
    messages[-1]["content"] += "\n" + marker
    assert not replay.check_invariants(messages, capture, fixture)["satisfied"]


def test_paired_system_inventory_includes_the_late_policy(replay_case):
    replay, messages, _, _ = replay_case
    changed = copy.deepcopy(messages)
    index = next(i for i, message in enumerate(changed) if "## Final instruction" in message["content"])
    changed[index]["content"] += "\nAdditional directive."
    assert replay.system_messages(messages) != replay.system_messages(changed)
    assert replay.system_messages(messages) == replay.system_messages(copy.deepcopy(messages))
