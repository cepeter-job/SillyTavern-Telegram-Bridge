"""Frozen full-story captures replayed through the real prompt builder and gate."""

from __future__ import annotations

import json
import tempfile
from collections import Counter
from dataclasses import asdict, replace
from functools import partial
from pathlib import Path

from evaluate_story_memory_answers import build_plan
from story_memory_eval_support import CHAT
from story_memory_retrieval_corpus import RetrievalCorpus
from story_memory_retrieval_fixture import sha256

from bridge.context_compaction import budget_chat_messages
from bridge.context_dispatch import prepare_context_dispatch
from bridge.context_selection_store import prepare_context_selection
from bridge.generation import build_chat_messages, format_user_dialogue_action
from bridge.light_novel_format import add_inline_contract
from bridge.settings import load_app_settings

FIXTURE = Path(__file__).resolve().parents[1] / "tests/fixtures/story_memory/context_efficiency_v2.json"
QUERIES = ("Q01", "Q02", "Q03", "Q16", "Q17", "Q23")


def json_hash(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False))


def build_replay(*, model=None, max_output_tokens=None):
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if model is not None:
        fixture["settings"]["model"] = model
    if max_output_tokens is not None:
        fixture["settings"]["max_tokens"] = max_output_tokens
    rows = []
    with tempfile.TemporaryDirectory(prefix="context-efficiency-") as home:
        with RetrievalCorpus(Path(home)) as corpus:
            # Reuse the established retrieval checkpoint and its scopes; its retrieval-only
            # messages and required-answer judgments never enter a story request.
            retrieval_rows, _, _ = build_plan(corpus, QUERIES)
            for capture, query_id, retrieval in zip(fixture["cases"], QUERIES, retrieval_rows, strict=True):
                scope = corpus.scopes[query_id]
                history = [tuple(row) for row in capture["history"]] * capture.get("history_repeat", 1)
                captured_story = None
                native_context = None
                variants = {}
                for variant in ("baseline", "candidate"):
                    session = dict(
                        fixture["session"], model_id=fixture["settings"]["model"], session_id=scope.session_id
                    )
                    session["response_language"] = capture.get("response_language", session["response_language"])
                    settings = fixture["settings"]
                    app_settings = load_app_settings(
                        {
                            "SILLYTAVERN_CONTEXT_WINDOW_TOKENS": str(settings["context_window_tokens"]),
                            "SILLYTAVERN_CONTEXT_INPUT_CAP_TOKENS": str(settings["context_input_cap_tokens"]),
                            "SILLYTAVERN_CONTEXT_SELECTION_MODE": "enabled" if variant == "candidate" else "off",
                            "SILLYTAVERN_CONTEXT_SELECTION_SLICES": "dedup" if variant == "candidate" else "",
                        },
                        home=Path(home),
                    )
                    service = replace(
                        corpus.runtime.memory,
                        select_context=partial(
                            prepare_context_selection,
                            app_settings=app_settings,
                            validate_blocks=corpus.runtime.memory.validate_blocks,
                        ),
                    )
                    memory_prompt = service.prompt_context(
                        corpus.db,
                        CHAT,
                        corpus.sessions[next(case.session_key for case in corpus.cases if case.query_id == query_id)],
                        fixture["fields"],
                        capture["user_text"],
                        through_rowid=scope.through_rowid if scope.historical else None,
                        principals=scope.principals,
                        historical=scope.historical,
                    )
                    if memory_prompt.scope != scope:
                        raise ValueError("native_capture_scope_mismatch")
                    contexts = dict(fixture["contexts"])
                    contexts.update(
                        memory_context=memory_prompt.recall,
                        episodic_context=memory_prompt.episodic,
                        session_summary=memory_prompt.summary,
                        scene_context=memory_prompt.scene,
                    )
                    if variant == "baseline":
                        native_context = memory_prompt
                        captured_story = {
                            "character": fixture["fields"],
                            "session": session,
                            "current_input": capture["user_text"],
                            "history": history,
                            "contexts": contexts,
                            "response_language": session["response_language"],
                            "preserve_last_assistant": capture.get("preserve_last_assistant", False),
                        }
                    selection = {
                        "reason": memory_prompt.selection_reason,
                        "deduplicated_blocks": memory_prompt.selection.deduplicated_blocks
                        if memory_prompt.selection
                        else 0,
                    }
                    messages = build_chat_messages(
                        session,
                        fixture["fields"],
                        capture["user_text"],
                        history,
                        persona_service=corpus.runtime.persona,
                        app_settings=app_settings,
                        defer_compaction=True,
                        memory_prompt=memory_prompt,
                        **contexts,
                    )
                    messages = add_inline_contract(messages, 4, session["response_language"])
                    messages, dispatch_stats = prepare_context_dispatch(
                        messages,
                        app_settings=app_settings,
                        model=settings["model"],
                        requested_output_tokens=settings["max_tokens"],
                    )
                    messages, stats = budget_chat_messages(
                        messages,
                        settings["model"],
                        settings["max_tokens"],
                        app_settings=app_settings,
                        preserve_last_assistant=capture.get("preserve_last_assistant", False),
                    )
                    stats.update(dispatch_stats)
                    invariants = check_invariants(messages, capture, fixture)
                    variants[variant] = {
                        "model": settings["model"],
                        "settings": {"max_tokens": settings["max_tokens"], "temperature": settings["temperature"]},
                        "messages": messages,
                        "prompt_sha256": json_hash(messages),
                        "estimated_input_tokens": stats["final_tokens"],
                        "context_stats": stats,
                        "selection": selection,
                        "invariants": invariants,
                    }
                system_preserved = system_messages(variants["baseline"]["messages"]) == system_messages(
                    variants["candidate"]["messages"]
                )
                variants["candidate"]["invariants"]["paired_system_content_preserved"] = system_preserved
                variants["candidate"]["invariants"]["satisfied"] &= system_preserved
                rows.append(
                    {
                        "case_id": capture["case_id"],
                        "category": capture["category"],
                        "weight": capture["weight"],
                        "retrieval_query_id": query_id,
                        "scope": asdict(scope),
                        "reference_retrieval_selected_fact_keys": retrieval["selected_fact_keys"],
                        "selected_fact_keys": sorted(
                            {
                                key
                                for pointer in native_context.evidence
                                for key, stored in corpus.facts.items()
                                if stored.memory_id == pointer.memory_id
                            }
                        ),
                        "captured_story": captured_story,
                        "variants": variants,
                    }
                )
    identity = {
        "fixture_sha256": sha256(FIXTURE.read_bytes()),
        "cases": [
            {
                "case_id": row["case_id"],
                "weight": row["weight"],
                "variants": {
                    name: {key: value[key] for key in ("prompt_sha256", "model", "settings")}
                    for name, value in row["variants"].items()
                },
            }
            for row in rows
        ],
    }
    return fixture, rows, json_hash(identity)


def system_messages(messages):
    """Preserve every system message, including native post-history policies."""
    return [message["content"] for message in messages if message["role"] == "system"]


def post_history_invariants(messages, fixture):
    post = fixture["fields"]["post_history_instructions"]
    if not post:
        return True, True
    matches = [
        (index, message["content"])
        for index, message in enumerate(messages)
        if message["role"] == "system" and post in message["content"]
    ]
    if len(matches) != 1 or matches[0][1].count(post) != 1:
        return False, False
    index, content = matches[0]
    history_end = max(
        (i for i, message in enumerate(messages[:-1]) if message["role"] in {"user", "assistant"}),
        default=0,
    )
    placement = history_end < index < len(messages) - 1
    native_markers = (
        "## Narrative Policy",
        "## Telegram Roleplay Output Contract",
        "## Canonical story state",
        "## Mandatory response language",
    )
    positions = [content.find(marker) for marker in native_markers]
    precedence = content.find(post) < positions[0] and positions == sorted(set(positions))
    return placement, precedence


def check_invariants(messages, capture, fixture):
    systems = system_messages(messages)
    mandatory = [
        fixture["fields"][key]
        for key in ("system_prompt", "description", "personality", "scenario", "post_history_instructions")
    ]
    mandatory += [
        fixture["session"]["system_prompt"],
        fixture["session"]["author_note"],
        fixture["contexts"]["group_context"],
        fixture["contexts"]["narrative_context"],
        "Mandatory response language",
        "Light Novel response contract",
        "## Telegram Roleplay Output Contract",
        "## Canonical story state",
    ]
    missing = [item for item in mandatory if not any(item in system for system in systems)]
    placement, precedence = post_history_invariants(messages, fixture)
    current = format_user_dialogue_action(capture["user_text"])
    current_preserved = messages[-1]["role"] == "user" and current in messages[-1]["content"]
    available = Counter((m["role"], m["content"]) for m in messages[1:-1])
    required = Counter(
        (role, format_user_dialogue_action(text) if role == "user" else text)
        for role, text in capture["protected_history"]
    )
    history_preserved = all(available[row] >= count for row, count in required.items())
    wire_text = json.dumps(messages, ensure_ascii=False).casefold()
    canary_excluded = all(canary.casefold() not in wire_text for canary in fixture["forbidden_prompt_canaries"])
    return {
        "satisfied": not missing
        and current_preserved
        and history_preserved
        and canary_excluded
        and placement
        and precedence,
        "post_history_placement_preserved": placement,
        "native_policy_precedence_preserved": precedence,
        "forbidden_canary_excluded": canary_excluded,
        "mandatory_instructions_preserved": not missing,
        "current_input_preserved": current_preserved,
        "protected_history_multiplicity_preserved": history_preserved,
        "continuation_target_preserved": history_preserved if capture.get("preserve_last_assistant") else None,
    }
