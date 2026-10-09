#!/usr/bin/env python3
"""Zero-network native hybrid shadow diagnostics; never write production SQLite."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bridge.context_hybrid_shadow import evaluate_hybrid_shadow  # noqa: E402
from bridge.context_hybrid_types import HISTORY_MARKER, MAX_HISTORY_ROWS  # noqa: E402
from bridge.memory_fact_store import digest_value  # noqa: E402
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
    "bridge/context_selection_runtime.py",
    "bridge/context_selection_store.py",
    "bridge/memory_contracts.py",
    "bridge/context_hybrid_types.py",
    "bridge/context_hybrid_sources.py",
    "bridge/context_hybrid_policy.py",
    "bridge/context_hybrid_shadow.py",
    "tools/hybrid_context_fixture.py",
    "tools/evaluate_hybrid_context.py",
)


def implementation_digest() -> str:
    return digest_value({name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in CODE_FILES})


def build_report(directory: Path) -> dict:
    cases = []
    for name in CASES:
        db, scope, messages, rows = native_fixture(directory, count=8 if name == "short_history" else 44, variant=name)
        try:
            if name == "large_character_card":
                messages[0]["content"] += " Mandatory card and world canon. " * 3000
            query = "astrolab pirus" if name == "indonesian" else "turquoise astrolabe"
            result = evaluate_hybrid_shadow(db, scope, messages, query=query)
            cases.append({"case_id": name, "weight": 1 / len(CASES), "source_rows": len(rows), **result.metrics})
        finally:
            db.close()
    baseline = sum(case["baseline_tokens"] for case in cases)
    candidate = sum(case["candidate_tokens"] for case in cases)
    return {
        "schema_version": 1,
        "type": "native_synthetic_hybrid_shadow",
        "implementation_sha256": implementation_digest(),
        "provider_requests": 0,
        "database_writes_to_production": 0,
        "production_activation_allowed": False,
        "semantic_equivalence_proven": False,
        "provider_measured_reduction": None,
        "estimated": {
            "baseline_tokens": baseline,
            "candidate_tokens": candidate,
            "aggregate_reduction_fraction": 1 - candidate / baseline,
            "case_weighted_reduction_fraction": sum(c["weight"] * c["estimated_reduction_fraction"] for c in cases),
        },
        "cases": cases,
        "limitations": [
            "Character-count estimates, not provider-reported input tokens.",
            "Accepted Summary provenance does not prove exhaustive causal extraction.",
            "This synthetic workload does not establish representative live-session savings.",
            "Negative controls are retained; actual dispatched prompts are unchanged full history.",
        ],
    }


def build_live_metadata(database: Path, settings) -> dict:
    """Inspect existing baseline history for the configured card's reader, export no text.

    This denominator is HISTORY ONLY, not a fabricated complete story prompt.
    World/card/system payloads are deliberately not rebuilt or sent anywhere.
    """
    from bridge.card_content import card_fields, read_png_chara, safe_character_path
    from bridge.memory_scope_store import resolve_memory_scope
    from bridge.user_dialogue import format_user_dialogue_action

    database = database.resolve(strict=True)
    cases = []
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=8) as db:
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("hybrid_live_database_integrity_failed")
        scopes = db.execute(
            "SELECT l.chat_id,l.session_id,s.character_file FROM memory_layer_state l JOIN sessions s "
            "ON s.chat_id=l.chat_id AND s.session_id=l.session_id AND s.created_at=l.session_created_at "
            "WHERE l.layer='summary' ORDER BY s.created_at LIMIT 65"
        ).fetchall()
        if len(scopes) > 64:
            raise ValueError("hybrid_live_scope_bound")
        for index, (chat, sid, character) in enumerate(scopes):
            fallback = {"sample": index + 1, "candidate_status": "fallback", "estimated_reduction_fraction": 0.0}
            try:
                card = safe_character_path(character, app_settings=settings)
                if card is None or card.stat().st_size > 16000000:
                    cases.append({**fallback, "reason": "reader_card_unavailable"})
                    continue
                fields = card_fields(read_png_chara(card), app_settings=settings)
                scope = resolve_memory_scope(db, chat, {"session_id": sid}, fields)
                raw = db.execute(
                    "SELECT role,content FROM messages WHERE chat_id=? AND session_id=? ORDER BY created_at,id LIMIT ?",
                    (chat, sid, MAX_HISTORY_ROWS + 1),
                ).fetchall()
                if scope is None or not raw or len(raw) > MAX_HISTORY_ROWS:
                    cases.append({**fallback, "reason": "scope_or_history_bound"})
                    continue
                history = [
                    {
                        "role": role,
                        "content": format_user_dialogue_action(text) if role == "user" else text,
                        HISTORY_MARKER: i,
                    }
                    for i, (role, text) in enumerate(raw)
                ]
                query = next((text for role, text in reversed(raw) if role == "user"), "")
                result = evaluate_hybrid_shadow(db, scope, history, query=query)
                cases.append({"sample": index + 1, "source_rows": len(raw), **result.metrics})
            except (ValueError, TypeError, KeyError, OSError, sqlite3.Error):
                cases.append({**fallback, "reason": "native_metadata_unavailable"})
        if db.total_changes:
            raise ValueError("hybrid_read_only_contract_violated")
    return {
        "schema_version": 1,
        "type": "read_only_live_history_shape",
        "provider_requests": 0,
        "production_transcript_exported": False,
        "database_writes": 0,
        "production_activation_allowed": False,
        "provider_measured_reduction": None,
        "denominator": "existing history only, with candidate continuity-reference overhead; not full story prompts",
        "query_policy": "latest existing user turn; no new model generation",
        "implementation_sha256": implementation_digest(),
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--metadata-only", action="store_true")
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        parser.error("output must be a new file; previous evidence is never overwritten")
    if args.database and not args.metadata_only:
        parser.error("live SQLite diagnostics require --metadata-only")
    if args.metadata_only and not args.database:
        parser.error("--metadata-only requires --database")
    if args.database:
        from bridge.environment import bootstrap_environment
        from bridge.settings import load_app_settings

        environ = dict(os.environ)
        bootstrap_environment(environ)
        report = build_live_metadata(args.database, load_app_settings(environ, home=Path.home()))
    else:
        with tempfile.TemporaryDirectory(prefix="hybrid-shadow-fixtures-") as directory:
            report = build_report(Path(directory))
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
