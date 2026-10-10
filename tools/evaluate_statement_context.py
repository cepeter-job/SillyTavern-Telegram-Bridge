#!/usr/bin/env python3
"""Provider-free statement-shadow trial using full synthetic generation prompts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bridge.card_content import card_fields, read_png_chara, safe_character_path  # noqa: E402
from bridge.context_hybrid_shadow import evaluate_hybrid_shadow  # noqa: E402
from bridge.context_hybrid_types import HISTORY_MARKER, MAX_HISTORY_ROWS  # noqa: E402
from bridge.context_statement_shadow import evaluate_statement_shadow  # noqa: E402
from bridge.generation import build_chat_messages  # noqa: E402
from bridge.memory_artifact_store import load_artifact_classification  # noqa: E402
from bridge.memory_contracts import MemoryPromptContext  # noqa: E402
from bridge.memory_fact_store import digest_value  # noqa: E402
from bridge.memory_scope_store import resolve_memory_scope  # noqa: E402
from bridge.settings import load_app_settings  # noqa: E402
from bridge.user_dialogue import format_user_dialogue_action  # noqa: E402
from tools.hybrid_context_fixture import native_fixture  # noqa: E402

CASES = (
    "unique_dialogue",
    "commitment_dense",
    "indonesian",
    "unsupported_script",
    "short_history",
    "large_character_card",
)
CODE_FILES = (
    "bridge/context_hybrid_sources.py",
    "bridge/context_hybrid_types.py",
    "bridge/context_hybrid_policy.py",
    "bridge/context_hybrid_shadow.py",
    "bridge/context_statement_policy.py",
    "bridge/context_statement_shadow.py",
    "bridge/context_selection_runtime.py",
    "bridge/context_selection_store.py",
    "bridge/memory_contracts.py",
    "bridge/generation.py",
    "tools/hybrid_context_fixture.py",
    "tools/evaluate_statement_context.py",
)


class _NoPersona:
    def name(self, _persona):
        return ""

    def get(self, _persona):
        return None


def implementation_digest() -> str:
    return digest_value({name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in CODE_FILES})


def _synthetic_messages(db, scope, rows, settings, variant="unique_dialogue") -> list[dict]:
    # The real prompt builder includes card/system/author/summary/current-turn.
    details = "A living fictional character with persistent motives and obligations. "
    if variant == "large_character_card":
        details *= 450
    fields = card_fields(
        {
            "name": "Rowan",
            "description": "CHARACTER_CARD: " + details,
            "personality": "Prudent, cooperative, observant.",
            "scenario": "World Scenario: an old copper museum beside a northern river.",
            "system_prompt": "CHARACTER_CARD_SYSTEM_RULES: Do not speak for the user. Respect known and unknown facts.",
            "post_history_instructions": "POST_HISTORY_DIRECTIVE preserve voice, user agency and consequences.",
            "mes_example": "<START> Rowan: The tide is rising. <END>",
        },
        app_settings=settings,
    )
    session = {
        "persona_id": "",
        "response_language": "auto",
        "system_prompt": "Continue chronological causality; do not retcon.",
        "model_id": "m",
        "world_file": "",
        "author_note": "Maintain already established commitments and reader knowledge.",
        "grounded_user": "off",
    }
    classified = load_artifact_classification(db, scope.chat_id, scope.session_id, "summary")
    if classified is None:
        raise ValueError("synthetic_native_summary_missing")
    summary = "\n".join(block["text"] for block in classified[2] if block["visibility"] == "shared")
    user_text = (
        "Jelaskan astrolab pirus tanpa memilih tindakan saya."
        if variant == "indonesian"
        else "Describe the turquoise astrolabe without choosing my next action."
    )
    return build_chat_messages(
        session,
        fields,
        user_text,
        [(role, text) for _, role, text in rows],
        persona_service=_NoPersona(),
        session_summary=summary,
        app_settings=settings,
        memory_prompt=MemoryPromptContext(scope=scope, selection_mode="shadow"),
        defer_compaction=True,
    )


def reconstruct_synthetic_prompt(directory: Path) -> list[dict]:
    db, scope, _, rows = native_fixture(directory)
    try:
        return _synthetic_messages(db, scope, rows, load_app_settings({}, home=directory))
    finally:
        db.close()


def build_report(directory: Path) -> dict:
    cases = []
    for variant in CASES:
        db, scope, _, rows = native_fixture(
            directory,
            count=8 if variant == "short_history" else 44,
            variant=variant,
        )
        try:
            messages = _synthetic_messages(db, scope, rows, load_app_settings({}, home=directory), variant)
            query = "astrolab pirus" if variant == "indonesian" else "turquoise astrolabe"
            old = evaluate_hybrid_shadow(db, scope, messages, query=query)
            current = evaluate_statement_shadow(db, scope, messages, query=query)
            baseline = current.metrics["baseline_tokens"]
            history_only = sum(8 + (len(text) + 3) // 4 for _, _, text in rows)
            cases.append(
                {
                    "case_id": variant,
                    "weight": 1 / len(CASES),
                    "source_rows": len(rows),
                    "fixed_prompt_tokens": baseline - history_only,
                    "old_hybrid": {
                        "candidate_status": old.metrics["candidate_status"],
                        "candidate_tokens": old.metrics["candidate_tokens"],
                        "estimated_reduction_fraction": old.metrics["estimated_reduction_fraction"],
                    },
                    **current.metrics,
                }
            )
        finally:
            db.close()
    baseline = sum(item["baseline_tokens"] for item in cases)
    candidate = sum(item["candidate_tokens"] for item in cases)
    old_candidate = sum(item["old_hybrid"]["candidate_tokens"] for item in cases)
    return {
        "type": "native_statement_shadow_complete_prompt",
        "schema_version": 1,
        "implementation_sha256": implementation_digest(),
        "provider_requests": 0,
        "production_database_writes": 0,
        "production_activation_allowed": False,
        "semantic_continuity_proven": False,
        "provider_measured_reduction": None,
        "estimated": {
            "complete_prompt_baseline_tokens": baseline,
            "complete_prompt_candidate_tokens": candidate,
            "old_hybrid_complete_prompt_candidate_tokens": old_candidate,
            "old_hybrid_aggregate_reduction_fraction": 1 - old_candidate / baseline,
            "aggregate_reduction_fraction": 1 - candidate / baseline,
            "case_weighted_reduction_fraction": sum(
                item["weight"] * item["estimated_reduction_fraction"] for item in cases
            ),
        },
        "cases": cases,
        "limitations": [
            "Full synthetic prompts, not measured provider request usage.",
            "Character-count estimates are not tokenizer- or provider-reported tokens.",
            "Source proof does not establish exhaustive causal interpretation.",
            "No negative control, fixed instruction or mandatory card is omitted.",
            "No new provider requests or production prompt modification occurred.",
        ],
    }


def build_live_metadata(database: Path, settings) -> dict:
    """Read-only live history-only estimate, omitting all private source strings."""
    samples = []
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=9)) as db, db:
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("statement_live_database_invalid")
        scoped = db.execute(
            "SELECT l.chat_id,l.session_id,s.character_file "
            "FROM memory_layer_state l JOIN sessions s "
            "ON s.chat_id=l.chat_id AND s.session_id=l.session_id "
            "AND s.created_at=l.session_created_at "
            "WHERE l.layer='summary' ORDER BY s.created_at LIMIT 65"
        ).fetchall()
        if len(scoped) > 64:
            raise ValueError("statement_live_scope_bound")
        for ordinal, (chat, sid, character) in enumerate(scoped):
            fallback = {
                "sample": ordinal + 1,
                "candidate_status": "fallback",
                "reason": "native_proof_unavailable",
                "estimated_reduction_fraction": 0.0,
                "production_activation_allowed": False,
            }
            try:
                card = safe_character_path(character, app_settings=settings)
                if card is None or card.stat().st_size > 16_000_000:
                    samples.append({**fallback, "reason": "reader_card_unavailable"})
                    continue
                fields = card_fields(read_png_chara(card), app_settings=settings)
                scope = resolve_memory_scope(db, chat, {"session_id": sid}, fields)
                raw = db.execute(
                    "SELECT role,content FROM messages WHERE chat_id=? AND session_id=? ORDER BY created_at,id LIMIT ?",
                    (chat, sid, MAX_HISTORY_ROWS + 1),
                ).fetchall()
                if scope is None or not raw or len(raw) > MAX_HISTORY_ROWS:
                    samples.append({**fallback, "reason": "scope_or_history_bound"})
                    continue
                messages = [
                    {
                        "role": role,
                        "content": format_user_dialogue_action(text) if role == "user" else text,
                        HISTORY_MARKER: i,
                    }
                    for i, (role, text) in enumerate(raw)
                ]
                query = next((text for role, text in reversed(raw) if role == "user"), "")
                whole = evaluate_hybrid_shadow(db, scope, messages, query=query)
                detail = evaluate_statement_shadow(db, scope, messages, query=query)
                samples.append(
                    {
                        "sample": ordinal + 1,
                        "source_rows": len(raw),
                        "old_hybrid_reason": whole.metrics["reason"],
                        "old_hybrid_estimated_reduction_fraction": whole.metrics["estimated_reduction_fraction"],
                        **detail.metrics,
                    }
                )
            except (ValueError, TypeError, KeyError, OSError, sqlite3.Error):
                samples.append(fallback)
        if db.total_changes:
            raise ValueError("statement_metadata_must_be_readonly")
    return {
        "type": "read_only_live_history_only_metadata",
        "schema_version": 1,
        "implementation_sha256": implementation_digest(),
        "provider_requests": 0,
        "production_database_writes": 0,
        "production_transcript_exported": False,
        "production_activation_allowed": False,
        "provider_measured_reduction": None,
        "denominator": "History and source-backed reference; fixed live prompts unavailable in this mode.",
        "cases": samples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--metadata-only", action="store_true")
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        parser.error("output must not overwrite an existing checkpoint")
    if bool(args.database) != bool(args.metadata_only):
        parser.error("live database requires explicit --metadata-only")
    if args.database:
        from bridge.environment import bootstrap_environment

        environ = dict(os.environ)
        bootstrap_environment(environ)
        report = build_live_metadata(args.database, load_app_settings(environ, home=Path.home()))
    else:
        with tempfile.TemporaryDirectory(prefix="statement-shadow-native-") as temp:
            report = build_report(Path(temp))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as file:
        os.chmod(args.output, 0o600)
        json.dump(report, file, ensure_ascii=False, indent=2, allow_nan=False)
        file.write("\n")
    print(
        json.dumps(
            {
                "type": report["type"],
                "cases": len(report["cases"]),
                "provider_requests": 0,
                "estimated": report.get("estimated"),
                "production_activation_allowed": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
