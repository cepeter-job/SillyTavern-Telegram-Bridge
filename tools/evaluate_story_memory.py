#!/usr/bin/env python3
"""Evaluate deterministic production story-memory contracts without external I/O."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import tracemalloc
from dataclasses import asdict
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from story_memory_eval_checks import (  # noqa: E402
    drain_cleanup,
    facts_only_boundary,
    measurement_metrics,
    purge_and_drain,
)
from story_memory_eval_support import CHAT, MARKER, EvaluationRuntime, marker_facts  # noqa: E402


def text_of(context):
    return "\n".join(value for value in (context.recall, context.episodic, context.summary, context.scene) if value)


class Score:
    def __init__(self):
        self.cases = []

    def case(self, case_id, category, checks, **details):
        assertions = [{"name": name, "passed": bool(value)} for name, value in checks.items()]
        self.cases.append(
            {
                "case_id": case_id,
                "category": category,
                "passed": all(x["passed"] for x in assertions),
                "assertions": assertions,
                **details,
            }
        )


def git_command(*arguments):
    # All callers below supply fixed read-only git subcommands.
    return subprocess.run(  # noqa: S603
        [shutil.which("git") or "/usr/bin/git", "-C", str(ROOT), *arguments],
        text=True,
        capture_output=True,
        check=False,
    )


def git_revision():
    result = git_command("rev-parse", "HEAD")
    return result.stdout.strip() if result.returncode == 0 else "unavailable"


def _evaluate(fixture_path, repeats=10, fault=""):
    """One isolated accepted story, fixed mutation sequence and measured queries."""
    from bridge.context_compaction import ContextWindowBudgetError, budget_chat_messages
    from bridge.memory_store import claim_jobs, next_source_segment, recover_expired_jobs, source_is_valid
    from bridge.memory_workers import dispatch_memory_backlog
    from bridge.sqlite_store import db_connect

    raw = fixture_path.read_bytes()
    fixture = json.loads(raw)
    score = Score()
    started = time.perf_counter_ns()
    with tempfile.TemporaryDirectory(prefix="story-memory-eval-") as temporary:
        with EvaluationRuntime(Path(temporary), fixture, fault) as runtime:
            db = runtime.db
            setup_ns = time.perf_counter_ns() - started
            ingest_start = time.perf_counter_ns()
            for user, assistant in zip(fixture["messages"][::2], fixture["messages"][1::2], strict=True):
                rows = runtime.accept_turn(user["content"], assistant["content"])
                runtime.map_message(user, rows[0])
                runtime.map_message(assistant, rows[1])
            ingest_ns = time.perf_counter_ns() - ingest_start
            score.case(
                "acceptance.primary",
                "acceptance",
                {
                    "canonical_messages": db.execute("SELECT count(*) FROM messages").fetchone()[0] == 300,
                    "accepted_variants": db.execute("SELECT count(*) FROM response_variants").fetchone()[0] == 150,
                    "durable_jobs_created": db.execute("SELECT count(*) FROM memory_jobs").fetchone()[0] == 6,
                },
                acceptance_route="session_core.create_session + message_commands.generate_and_store_reply",
                derived_rows_seeded=0,
            )
            drain_start = time.perf_counter_ns()
            episode_outcomes = runtime.drain("episodes")
            hindsight_outcomes = runtime.drain("hindsight")
            drain_ns = time.perf_counter_ns() - drain_start
            facts = runtime.fact_mapping()
            target = facts["f0005"][0] if facts["f0005"] else {}
            target_id = target.get("memory_id", 0)
            target_row = runtime.mapping["main:m0005"]["row_id"]
            counts = {
                "canonical_message_count": db.execute("SELECT count(*) FROM messages").fetchone()[0],
                "distinct_accepted_episode_count": db.execute(
                    "SELECT count(*) FROM memory_fact_provenance WHERE valid=1"
                ).fetchone()[0],
                "target_later_message_count": db.execute(
                    "SELECT count(*) FROM messages WHERE session_id='main' AND id>?", (target_row,)
                ).fetchone()[0],
                "target_later_episode_count": db.execute(
                    "SELECT count(DISTINCT e.memory_id) FROM episodic_memories e "
                    "JOIN memory_fact_provenance p ON p.memory_id=e.memory_id AND p.valid=1 "
                    "WHERE e.session_id='main' AND e.memory_id>?",
                    (target_id,),
                ).fetchone()[0],
            }
            score.case(
                "distant.prerequisites",
                "distant",
                {
                    "unique_early_target": len(facts["f0005"]) == 1,
                    "later_messages_over_100": counts["target_later_message_count"] > 100,
                    "later_distinct_episodes_over_200": counts["target_later_episode_count"] > 200,
                    "no_recent_duplicate": db.execute(
                        "SELECT count(*) FROM episodic_memories WHERE summary=?",
                        ("The amber compass is sealed in the east attic.",),
                    ).fetchone()[0]
                    == 1,
                },
            )
            mapping_valid = True
            for mappings in facts.values():
                for mapped in mappings:
                    pointer = mapped["evidence"]
                    content = db.execute(
                        "SELECT content FROM messages WHERE id=?", (pointer["source_start_rowid"],)
                    ).fetchone()[0]
                    mapping_valid &= (
                        pointer["source_start_rowid"] == pointer["source_end_rowid"]
                        and pointer["start_offset"] <= mapped["annotated_start_char"]
                        and pointer["end_offset"] >= mapped["annotated_end_char"]
                        and mapped["summary"] in content[mapped["annotated_start_char"] : mapped["annotated_end_char"]]
                    )
            score.case(
                "evidence.canonical-mapping",
                "evidence",
                {
                    "all_expected_attestations_accepted": all(facts.values()),
                    "all_marker_spans_in_actual_source_parts": mapping_valid,
                },
                mapped_fact_key_count=len(facts),
            )
            # Warm-up and samples use the same full production query path.
            runtime.context("amber compass east attic")
            samples = []
            for _ in range(repeats):
                qstart = time.perf_counter_ns()
                runtime.context("amber compass east attic")
                samples.append((time.perf_counter_ns() - qstart) / 1_000_000)
            for truth in fixture["truth"]:
                boundary = runtime.mapping[truth["as_of_message_key"]]["row_id"]
                context = runtime.context(truth["query"], truth["reader"], boundary)
                output = text_of(context)
                required = [item["summary"] for key in truth["required_fact_keys"] for item in facts[key]]
                forbidden = [item["summary"] for key in truth["forbidden_fact_keys"] for item in facts[key]]
                score.case(
                    truth["case_id"],
                    "knowledge" if truth["case_id"].startswith("knowledge") else "distant",
                    {
                        "required_fact_present": all(value in output for value in required),
                        "forbidden_memory_fact_absent": all(value not in output for value in forbidden),
                        "required_canonical_source_pointer": all(
                            any(e.memory_id == mapped["memory_id"] for e in context.evidence)
                            for key in truth["required_fact_keys"]
                            for mapped in facts[key]
                        ),
                    },
                    reader=truth["reader"],
                    as_of_row_id=boundary,
                    evidence=[asdict(e) for e in context.evidence],
                )
            alias = runtime.context("bearing relic")
            score.case(
                "semantic.alias",
                "semantic",
                {
                    "retained_candidate_rehydrated": "The amber compass is sealed in the east attic." in text_of(alias),
                    "remote_enrichment_excluded": "UNTRUSTED" not in text_of(alias),
                    "semantic_pointer_has_document": any(e.document_id for e in alias.evidence),
                },
            )
            from hindsight_client_api.models.recall_result import RecallResult

            runtime.transport.extra_candidates = [
                RecallResult(id="unmapped", document_id="not-locally-mapped", text="FOREIGN RAW SECRET", type="world"),
                RecallResult(
                    id="observation",
                    document_id=next(iter(runtime.transport.documents_by_id)),
                    text="UNKNOWN OBSERVATION",
                    type="observation",
                ),
            ]
            closure = runtime.context("vault phrase violet heron", "Rowan", runtime.mapping["main:m0019"]["row_id"])
            score.case(
                "knowledge.mixed-closure",
                "knowledge",
                {
                    "secret_memory_excluded": "violet heron" not in text_of(closure),
                    "unmapped_and_observation_excluded": not any(
                        value in text_of(closure) for value in ["FOREIGN", "UNKNOWN", "UNTRUSTED"]
                    ),
                    "mixed_canonical_row_contains_secret": "violet heron"
                    in db.execute(
                        "SELECT content FROM messages WHERE id=?", (runtime.mapping["main:m0019"]["row_id"],)
                    ).fetchone()[0],
                },
                raw_history_secrecy_claim=False,
            )
            # Execute a real final request and persist it; history is inventoried separately.
            payload_count = len(runtime.transport.story_payloads)
            runtime.accept_turn("Where is the amber compass?")
            payload = runtime.transport.story_payloads[-1]
            admitted, budget = budget_chat_messages(
                payload["messages"],
                "offline::fixture",
                int(payload["settings"]["max_tokens"]),
                app_settings=runtime.settings,
                compact=False,
            )
            restricted_phrase = "violet heron"
            system_inputs = "\n".join(str(m["content"]) for m in admitted if m["role"] == "system")
            early_payload = runtime.transport.story_payloads[10]["messages"]
            raw_history_inputs = "\n".join(str(m["content"]) for m in early_payload[1:-1])
            other_early_inputs = str(early_payload[0]["content"]) + str(early_payload[-1]["content"])
            score.case(
                "knowledge.raw-history-exposure",
                "knowledge",
                {
                    "derived_memory_excludes_unknown_fact": "violet heron" not in text_of(closure),
                    "raw_prompt_independently_contains_unknown_fact": "violet heron" in raw_history_inputs,
                },
                captured_primary_turn=11,
                reader="Rowan",
                claim="Derived memory eligibility does not filter raw transcript inputs",
            )
            score.case(
                "budget.accepted-final-request",
                "budget",
                {
                    "actual_provider_payload_captured": len(runtime.transport.story_payloads) == payload_count + 1,
                    "post_history_instruction_present": "Preserve accepted survey facts." in system_inputs,
                    "required_fact_in_actual_prompt": "The amber compass is sealed in the east attic." in str(admitted),
                    "actual_request_fits": not budget["over_budget"],
                    "actual_output_reservation": budget["requested_output_tokens"] == payload["settings"]["max_tokens"],
                },
                budget=budget,
            )
            before = db.execute("SELECT count(*) FROM messages").fetchone()[0]
            dispatch_before = len(runtime.transport.story_payloads)
            original_prompt = runtime.fields["system_prompt"]
            runtime.fields["system_prompt"] = "Protected fixed instructions " + "x" * 300000
            overflow = False
            try:
                runtime.accept_turn("This protected request cannot fit.")
            except ContextWindowBudgetError:
                overflow = True
            finally:
                runtime.fields["system_prompt"] = original_prompt
            score.case(
                "budget.protected-overflow",
                "budget",
                {
                    "explicit_budget_error": overflow,
                    "no_provider_dispatch": len(runtime.transport.story_payloads) == dispatch_before,
                    "no_generated_turn_persisted": db.execute("SELECT count(*) FROM messages").fetchone()[0] == before,
                },
            )
            # A complete marker appears beyond 1800, 74000 and 150000 code points.
            long_rows = runtime.accept_turn(fixture["long_message"])
            long_id = long_rows[0][0]
            long_outcomes = runtime.drain("episodes")
            long_context = runtime.context("long scroll head middle tail copper indigo jade")
            parts = db.execute(
                "SELECT start_offset,end_offset FROM memory_segments "
                "WHERE layer='episodes' AND start_id=? AND valid=1 ORDER BY start_offset",
                (long_id,),
            ).fetchall()
            covered = sum(end - start for start, end in parts)
            score.case(
                "coverage.complete-long-source",
                "coverage",
                {
                    "head_middle_tail_extracted": all(
                        value in text_of(long_context) for value in fixture["long_required"]
                    ),
                    "all_source_characters_covered": covered == len(fixture["long_message"]),
                    "contiguous_source_parts": bool(parts)
                    and parts[0][0] == 0
                    and all(a[1] == b[0] for a, b in pairwise(parts)),
                    "more_than_one_worker_window": "deferred" in long_outcomes,
                    "bounded_part_size": all(end - start <= 12000 for start, end in parts),
                },
                source_row_id=long_id,
                covered_chars=covered,
                total_chars=len(fixture["long_message"]),
                source_parts=parts,
            )
            source_probe = fixture["long_message"]
            stripped = source_probe.replace(
                next(m.group(0) for m in MARKER.finditer(source_probe) if m.group(1) == "tail"), ""
            )
            score.case(
                "transport.input-sensitivity",
                "coverage",
                {
                    "tail_without_input_not_invented": fixture["long_required"][-1]
                    not in [fact["summary"] for fact in marker_facts(stripped)],
                    "tail_with_complete_input_extracted": fixture["long_required"][-1]
                    in [fact["summary"] for fact in marker_facts(source_probe)],
                },
            )
            # Failure is at the real client's retain seam. State/ACK remain production.
            runtime.transport.fail_retain = True
            retain_before = len(runtime.transport.calls)
            failed = runtime.run_one("hindsight")
            pending = db.execute("SELECT count(*) FROM memory_fact_index WHERE state='pending'").fetchone()[0]
            runtime.transport.fail_retain = False
            score.case(
                "retry.failed-retain",
                "retry",
                {
                    "retain_failure_reported": failed == "retain_failed",
                    "native_indexes_remain_pending": pending > 0,
                    "external_attempt_occurred": len(runtime.transport.calls) > retain_before,
                },
            )
            remote_ids_before = set(runtime.transport.documents_by_id)

            def lose_ack():
                db.execute("UPDATE memory_jobs SET lease_token='',lease_deadline=0 WHERE layer='hindsight'")
                db.commit()

            runtime.transport.after_retain = lose_ack
            lost_ack = runtime.run_one("hindsight")
            successful_remote_id = set(runtime.transport.documents_by_id) - remote_ids_before
            pending_before_retry = {
                row[0] for row in db.execute("SELECT document_id FROM memory_fact_index WHERE state='pending'")
            }
            runtime.drain("hindsight")
            score.case(
                "retry.remote-success-lost-ack",
                "retry",
                {
                    "lost_ownership_not_acknowledged": lost_ack == "stale_source",
                    "remote_success_happened": bool(successful_remote_id),
                    "retry_same_native_identity": bool(successful_remote_id.intersection(pending_before_retry)),
                    "retry_indexes_complete": db.execute(
                        "SELECT count(*) FROM memory_fact_index WHERE state='pending'"
                    ).fetchone()[0]
                    == 0,
                },
                injected_state="synthetic lease ownership loss after successful external retain",
            )
            facts_only_boundary(runtime, score)
            # Capture a real source, append harmlessly, then mutate only the narrow source.
            runtime.accept_turn("A narrow mutation fixture.")
            captured = next_source_segment(db, CHAT, "main", "episodes")
            runtime.accept_turn("A harmless append.")
            append_valid = source_is_valid(db, captured)
            db.execute("UPDATE messages SET content='Rewritten narrow fixture' WHERE id=?", (captured.start_id,))
            db.commit()
            score.case(
                "rewrite.capture-append-and-edit",
                "rewrite",
                {
                    "harmless_append_preserves_source": append_valid,
                    "source_rewrite_invalidates_capture": not source_is_valid(db, captured),
                },
                fixture_scope="narrow canonical SQL mutation; primary story used production acceptance",
            )
            runtime.due("episodes")
            lease = claim_jobs(db, layers=("episodes",), chat_id=CHAT, session_id="main", now=time.time())[0]
            deadline = db.execute(
                "SELECT lease_deadline FROM memory_jobs WHERE lease_token=?", (lease.token,)
            ).fetchone()[0]
            score.case(
                "retry.active-versus-expired-lease",
                "retry",
                {
                    "active_lease_not_stolen": not claim_jobs(db, layers=("episodes",), now=deadline - 1),
                    "active_recovery_noop": recover_expired_jobs(db, now=deadline - 1) == 0,
                    "expired_lease_recovered": recover_expired_jobs(db, now=deadline + 1) == 1,
                },
            )
            services = SimpleNamespace(
                config=runtime.settings, background=SimpleNamespace(submit=lambda *a, **k: False)
            )
            rejected = dispatch_memory_backlog(services, db)
            score.case(
                "retry.executor-rejection",
                "retry",
                {
                    "executor_rejected": rejected == 0,
                    "lease_released": db.execute("SELECT count(*) FROM memory_jobs WHERE lease_token<>''").fetchone()[0]
                    == 0,
                    "rejection_is_durable": db.execute(
                        "SELECT count(*) FROM memory_jobs WHERE last_error='executor_rejected'"
                    ).fetchone()[0]
                    > 0,
                },
            )
            runtime.db.close()
            runtime.db = db_connect(app_settings=runtime.settings)
            db = runtime.db
            score.case(
                "retry.restart",
                "retry",
                {
                    "canonical_story_survives": db.execute("SELECT count(*) FROM messages").fetchone()[0] > 300,
                    "pending_work_survives": db.execute(
                        "SELECT count(*) FROM memory_jobs WHERE dirty_version>completed_version"
                    ).fetchone()[0]
                    > 0,
                    "local_evidence_survives": "amber compass" in text_of(runtime.context("amber compass")),
                },
            )
            page_bytes = db.execute("PRAGMA page_count").fetchone()[0] * db.execute("PRAGMA page_size").fetchone()[0]
            wal = Path(str(runtime.settings.db_file) + "-wal")
            wal_bytes = wal.stat().st_size if wal.exists() else 0
            pending_layers = [
                dict(zip(["layer", "dirty_version", "completed_version"], row, strict=True))
                for row in db.execute("SELECT layer,dirty_version,completed_version FROM memory_jobs")
            ]
            # Commit local authority revocation, then execute the queued cleanup workers.
            purged, local_denied = purge_and_drain(runtime)
            retain_calls_before = sum(x["kind"] == "retain" for x in runtime.transport.calls)
            runtime.drain("hindsight")
            fresh = runtime.context("amber compass")
            score.case(
                "purge.floor",
                "purge",
                {
                    "local_authority_denied_before_cleanup": local_denied,
                    "external_documents_deleted": purged > 0 and not runtime.transport.documents_by_id,
                    "old_sources_not_automatically_retained": sum(
                        x["kind"] == "retain" for x in runtime.transport.calls
                    )
                    == retain_calls_before,
                    "native_local_fact_usable": "amber compass" in fresh.episodic,
                    "purged_external_rank_absent": fresh.recall == "",
                },
            )
            from bridge.memory_scope_store import scope_is_current
            from bridge.session_core import create_session, delete_session_data

            old_scope = fresh.scope
            create_session(db, CHAT, "offline::fixture", session_id="other", app_settings=runtime.settings)
            deleted, reason = delete_session_data(db, CHAT, "main", "other", memory_service=runtime.memory)
            drain_cleanup(runtime)
            recreated = create_session(db, CHAT, "offline::fixture", session_id="main", app_settings=runtime.settings)
            score.case(
                "incarnation.delete-recreate",
                "incarnation",
                {
                    "production_delete_succeeded": deleted and reason == "deleted",
                    "same_visible_key_recreated": recreated["session_id"] == "main",
                    "old_scope_rejected": not scope_is_current(db, old_scope),
                    "old_native_evidence_excluded": not text_of(runtime.context("amber compass")),
                },
            )
            score.case(
                "isolation.and-work-bounds",
                "isolation",
                {
                    "unconfigured_io_attempts_zero": not runtime.unexpected_io,
                    "all_claims_within_eight_external_calls": all(
                        item["external_calls"] <= 8 for item in runtime.claim_observations
                    ),
                    "bounded_extraction_sources": all(
                        item["chars"] <= 12000 for item in runtime.transport.extraction_sources
                    ),
                },
            )
            _, heap_peak = tracemalloc.get_traced_memory()
            report = {
                "schema_version": 2,
                "fixture_version": fixture["fixture_version"],
                "fixture_sha256": hashlib.sha256(raw).hexdigest(),
                "git_revision": git_revision(),
                "measurement_identity": {
                    "source_sha256": {
                        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                        for name in [
                            "tools/evaluate_story_memory.py",
                            "tools/story_memory_eval_support.py",
                            "tools/story_memory_eval_checks.py",
                        ]
                    },
                    "git_status_short": git_command("status", "--short").stdout.splitlines(),
                },
                "measurement_boundary": "Synthetic SQLite; native summaries and scripted mutation cases.",
                "python_version": platform.python_version(),
                "sqlite_version": sqlite3.sqlite_version,
                "synthetic_settings": {
                    "context_window_tokens": runtime.settings.context_window_tokens,
                    "history_candidates": runtime.settings.context_history_candidates,
                    "model": "offline::fixture",
                    "external_services": "offline scripted adapters",
                },
                "counts": counts,
                "cases": score.cases,
                "message_mapping": runtime.mapping,
                "fact_mapping": facts,
                "layer_outcomes": {"episodes": episode_outcomes, "hindsight": hindsight_outcomes},
                "pending_layers_at_observation": pending_layers,
                "metrics": measurement_metrics(
                    runtime, repeats, samples, setup_ns, ingest_ns, drain_ns, heap_peak, page_bytes, wal_bytes
                ),
                "retained_payload_observations": runtime.transport.native_payloads,
                "exposure_inventory": {
                    "memory_channel_forbidden_span_count": text_of(closure).count(restricted_phrase),
                    "raw_history_forbidden_span_count": raw_history_inputs.count(restricted_phrase),
                    "other_prompt_input_forbidden_span_count": other_early_inputs.count(restricted_phrase),
                    "raw_history_capture_primary_turn": 11,
                    "interpretation": "Scoped memory exclusion does not filter independent raw prompt inputs.",
                },
                "unexpected_network_attempt_count": len(runtime.unexpected_io),
                "worker_claim_observations": runtime.claim_observations,
                "extraction_source_observations": runtime.transport.extraction_sources,
                "limits": [
                    "Scripted semantic ranking/classification proves transport contracts, not real model quality.",
                    "Other routes, branches, drafts and malformed extraction are pytest-only; see documentation.",
                    "Four derived layers intentionally remain queued during primary episode/index measurement.",
                    "Python traced heap is not process RSS. Timings are observations, never pass/fail gates.",
                ],
            }
    report["executed_case_count"] = len(score.cases)
    report["passed_case_count"] = sum(case["passed"] for case in score.cases)
    report["failed_case_count"] = len(score.cases) - report["passed_case_count"]
    report["assertion_count"] = sum(len(case["assertions"]) for case in score.cases)
    report["scenario_counts_by_category"] = {
        category: sum(case["category"] == category for case in score.cases)
        for category in sorted({case["category"] for case in score.cases})
    }
    return report


def evaluate(fixture_path, repeats=10, fault=""):
    """Always release the heap tracer, including fatal setup/contract errors."""
    tracemalloc.start()
    try:
        return _evaluate(fixture_path, repeats, fault)
    finally:
        tracemalloc.stop()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=ROOT / "tests/fixtures/story_memory/v1.json")
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--inject-failure", choices=["drop-tail"], default="")
    parser.add_argument("--output", type=Path, help="Also write the same JSON report to this path.")
    args = parser.parse_args(argv)
    if not 3 <= args.repeats <= 100:
        parser.error("--repeats must be between 3 and 100")
    try:
        report = evaluate(args.fixture, args.repeats, args.inject_failure)
    except Exception as exc:
        report = {
            "schema_version": 2,
            "failed_case_count": 1,
            "fatal_error": {"type": type(exc).__name__, "message": str(exc)},
        }
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 1 if report["failed_case_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
