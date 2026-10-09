#!/usr/bin/env python3
"""Freeze paired native SQLite prompt plans; planning performs zero model requests."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
import sys
from dataclasses import asdict, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from native_context_fixture import FIXTURE_VERSION, declared_cases, workload_hash  # noqa: E402
from story_memory_eval_support import CHAT, EvaluationRuntime  # noqa: E402

from bridge import memory  # noqa: E402
from bridge.context_compaction import estimate_message_tokens  # noqa: E402
from bridge.context_history_codec import HISTORY_INDEX, pack_history  # noqa: E402
from bridge.context_native_receipt import capture_native_history, verify_native_candidate  # noqa: E402
from bridge.generation import build_chat_messages  # noqa: E402
from bridge.memory_contracts import MemoryReadScope  # noqa: E402
from bridge.memory_scope_store import resolve_memory_scope  # noqa: E402
from bridge.provider_port import ProviderPort  # noqa: E402


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def wire_messages(messages: list[dict]) -> list[dict]:
    return [{"role": message["role"], "content": message["content"]} for message in messages]


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _variant(messages: list[dict], model: str, max_output_tokens: int) -> dict:
    wire = wire_messages(messages)
    return {
        "model": model,
        "settings": {"max_tokens": max_output_tokens, "temperature": 0.7},
        "messages": wire,
        "annotated_messages": messages,
        "prompt_sha256": digest(wire),
        "estimated_input_tokens": estimate_message_tokens(wire),
    }


def _build_case(directory: Path, case: dict, model: str, max_output_tokens: int) -> dict:
    case_id = case["case_id"]
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", case_id):
        raise ValueError("invalid_native_case_id")
    home = directory / (case_id + "-runtime")
    home.mkdir()
    reference = json.loads((ROOT / "tests/fixtures/story_memory/context_efficiency_v1.json").read_text())
    with EvaluationRuntime(home, {"aliases": {}}) as runtime:
        for user, assistant in zip(case["history"][::2], case["history"][1::2], strict=True):
            if user[0] != "user" or assistant[0] != "assistant":
                raise ValueError("invalid_native_case_roles")
            runtime.accept_turn(user[1], assistant[1])
        # Scripted test output is never semantic authority: the proof below
        # retains every literal source, rather than trusting this summary.
        scripted = ProviderPort(
            lambda *_a, **_k: json.dumps(
                {
                    "blocks": [
                        {
                            "text": "This synthetic checkpoint covers the recorded dialogue; original evidence "
                            "remains in the transcript.",
                            "visibility": "shared",
                            "known_by": [],
                        }
                    ]
                }
            )
        )
        for _ in range(64):
            outcome = memory.generate_session_summary_result(
                runtime.db,
                CHAT,
                runtime.session,
                force=True,
                durable=True,
                max_segments=8,
                provider_port=scripted,
                app_settings=runtime.settings,
            )
            if outcome.complete:
                break
        else:
            raise ValueError("native_fixture_catchup_failed")
        fields = dict(reference["fields"])
        fields["name"] = "Rowan"
        session = dict(runtime.session, **reference["session"])
        session.update(session_id="main", model_id=model, response_language=case["language"])
        context = runtime.memory.prompt_context(runtime.db, CHAT, runtime.session, fields, case["request"])
        scope = resolve_memory_scope(runtime.db, CHAT, runtime.session, fields)
        if scope is None or context.scope != scope:
            raise ValueError("native_fixture_scope_mismatch")
        rows = runtime.db.execute(
            "SELECT role,content FROM messages WHERE chat_id=? AND session_id='main' ORDER BY created_at,id", (CHAT,)
        ).fetchall()
        contexts = dict(reference["contexts"])
        contexts.update(
            memory_context=context.recall,
            episodic_context=context.episodic,
            session_summary=context.summary,
            scene_context=context.scene,
        )
        built = build_chat_messages(
            session,
            fields,
            case["request"],
            rows,
            persona_service=runtime.persona,
            # Shadow tagging only: defer_compaction returns before any dispatch or selection.
            app_settings=runtime.settings,
            defer_compaction=True,
            memory_prompt=replace(context, selection_mode="shadow"),
            **contexts,
        )
        baseline = [{key: item[key] for key in ("role", "content", HISTORY_INDEX) if key in item} for item in built]
        candidate, selection = pack_history(baseline)
        receipt = capture_native_history(runtime.db, scope, baseline)
        proof = verify_native_candidate(runtime.db, receipt, baseline, candidate, current_scope=scope)
        if not proof["all_source_evidence_preserved"]:
            raise ValueError("native_fixture_continuity_failed")
        if selection["reason"] != case["expected_candidate"]:
            raise ValueError("native_fixture_selection_unexpected")
        snapshot = directory / (case_id + ".sqlite3")
        with sqlite3.connect(snapshot) as destination:
            runtime.db.backup(destination)
        prepared = {
            "case_id": case_id,
            "category": case["category"],
            "weight": case["weight"],
            "language": case["language"],
            "request": case["request"],
            "review_canon": case["review_canon"],
            "forbidden_canaries": case["forbidden_canaries"],
            "scope": asdict(scope),
            "snapshot_file": snapshot.name,
            "snapshot_sha256": file_digest(snapshot),
            "selection": selection,
            "proof": proof,
            "receipt_sha256": digest(asdict(receipt)),
            "variants": {
                "baseline": _variant(baseline, model, max_output_tokens),
                "candidate": _variant(candidate, model, max_output_tokens),
            },
            "native_acceptance": "session_core.create_session + message_commands.generate_and_store_reply + "
            "durable Summary worker",
            "native_source_kind": "synthetic_story_accepted_by_native_sqlite_pipeline",
            "provider_preparation_requests": 0,
        }
    revalidate_case(directory, prepared)
    return prepared


def revalidate_case(directory: Path, case: dict) -> dict:
    """Read-only source revalidation immediately before each experimental dispatch."""
    name = case["snapshot_file"]
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}\.sqlite3", name):
        raise ValueError("invalid_native_snapshot_path")
    path = (directory / name).resolve()
    if path.parent != directory.resolve() or file_digest(path) != case["snapshot_sha256"]:
        raise ValueError("native_snapshot_changed")
    values = dict(case["scope"])
    values["principals"] = tuple(values["principals"])
    scope = MemoryReadScope(**values)
    variants = case["variants"]
    for variant in variants.values():
        if wire_messages(variant["annotated_messages"]) != variant["messages"]:
            raise ValueError("native_wire_payload_changed")
        if digest(variant["messages"]) != variant["prompt_sha256"]:
            raise ValueError("native_wire_digest_changed")
    baseline = variants["baseline"]["annotated_messages"]
    candidate = variants["candidate"]["annotated_messages"]
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=8) as db:
        receipt = capture_native_history(db, scope, baseline)
        if digest(asdict(receipt)) != case["receipt_sha256"]:
            raise ValueError("native_receipt_changed")
        proof = verify_native_candidate(db, receipt, baseline, candidate, current_scope=scope)
        if not proof["all_source_evidence_preserved"]:
            raise ValueError("native_continuity_failed")
        return proof


def build_native_plan(
    directory: Path,
    *,
    model: str,
    max_output_tokens: int,
    cases: list[dict] | None = None,
) -> dict:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", model):
        raise ValueError("invalid_trial_model")
    if type(max_output_tokens) is not int or not 256 <= max_output_tokens <= 1000:
        raise ValueError("invalid_trial_output_budget")
    declarations = declared_cases() if cases is None else cases
    if not declarations or len(declarations) > 6 or not math.isclose(sum(c["weight"] for c in declarations), 1):
        raise ValueError("invalid_trial_cases")
    rows = [_build_case(directory, case, model, max_output_tokens) for case in declarations]
    totals = {
        name: sum(case["variants"][name]["estimated_input_tokens"] for case in rows)
        for name in ("baseline", "candidate")
    }
    weighted = sum(
        case["weight"]
        * (
            1
            - case["variants"]["candidate"]["estimated_input_tokens"]
            / case["variants"]["baseline"]["estimated_input_tokens"]
        )
        for case in rows
    )
    plan = {
        "schema_version": 1,
        "fixture_version": FIXTURE_VERSION,
        "workload_sha256": workload_hash(declarations),
        "network": {"requests": 0},
        "model": model,
        "cases": rows,
        "production_activation_allowed": False,
        "estimated": {
            **totals,
            "aggregate_reduction_fraction": 1 - totals["candidate"] / totals["baseline"],
            "weighted_reduction_fraction": weighted,
        },
        "budget": {
            "maximum_requests": 24,
            "maximum_logical_input_tokens": 300000,
            "maximum_output_tokens": 24000,
            "maximum_request_bytes": 150000,
            "minimum_subscription_reserve": 100000,
            "paid_overage": False,
        },
        "review": {"type": "automated_label_blinded_order_swapped", "human_approval": False},
        "limitations": [
            "Dictionary eligibility depends on repetition; controls and old frozen replay must not be hidden.",
            "Native exact-source evidence retention is not proof of a stochastic model's semantic behavior.",
            "Synthetic stories are never representative-production traffic measurements.",
            "A model judge is not an independent human reviewer and cannot authorize production activation.",
        ],
    }
    plan["plan_sha256"] = digest(plan)
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--model", default="nano-gpt::z-ai/glm-5.2")
    args = parser.parse_args()
    if (args.directory / "plan.json").exists():
        parser.error("immutable trial plan already exists")
    plan = build_native_plan(args.directory, model=args.model, max_output_tokens=1000)
    (args.directory / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "plan_sha256": plan["plan_sha256"],
                "estimated": plan["estimated"],
                "cases": len(plan["cases"]),
                "provider_requests": 0,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
