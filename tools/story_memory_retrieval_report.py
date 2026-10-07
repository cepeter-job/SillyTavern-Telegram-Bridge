"""Offline repeated measurements and one-attempt live observations share a report."""

from __future__ import annotations

from dataclasses import asdict
from time import perf_counter_ns

from story_memory_retrieval_artifacts import HAND_VECTORS, hand_vectors
from story_memory_retrieval_fixture import sha256
from story_memory_retrieval_identity import corpus_identity, runtime_identity, sqlite_footprint
from story_memory_retrieval_metrics import (
    aggregate_backend,
    observe_component,
    observe_fusion,
    paired_differences,
    query_base,
)
from story_memory_retrieval_rank import fts_rank
from story_memory_retrieval_vectors import BlobIndex, blob_rank


def unavailable(
    corpus, evidence_kind, *, reason="capability_or_budget_unverified", status="skipped", selected_set=False
):
    rows = [
        {
            **query_base(corpus, case),
            "status": status,
            "reason": reason,
            "admitted_fact_keys": [],
            "forbidden_admitted_count": 0,
        }
        for case in corpus.cases
    ]
    return {**aggregate_backend(rows, evidence_kind, selected_set=selected_set), "reason": reason}


def measure_offline(corpus, rank_call, evidence_kind, repeats, *, channel="recall"):
    rows, selected = [], []
    for case in corpus.cases:
        component_samples, selected_samples = {}, {}
        for sample in range(repeats + 1):
            start = perf_counter_ns()
            rank = rank_call(case)
            rank_ns = perf_counter_ns() - start
            row, final = observe_component(corpus, case, rank)
            row["timing_ns"]["component_total"] = rank_ns + final.timing_ns
            fused = observe_fusion(corpus, case, final if channel == "local_embedding" else None, channel=channel)
            fused["timing_ns"]["cached_pipeline_total"] = fused["timing_ns"]["fts_read_and_production_validation"]
            if channel == "local_embedding":
                fused["timing_ns"]["cached_pipeline_total"] += row["timing_ns"]["component_total"]
            if sample:  # Exactly one excluded warm-up of each full local path.
                for stage, value in row["timing_ns"].items():
                    component_samples.setdefault(stage, []).append(value)
                for stage, value in fused["timing_ns"].items():
                    selected_samples.setdefault(stage, []).append(value)
        row["timing_samples_ns"], fused["timing_samples_ns"] = component_samples, selected_samples
        rows.append(row)
        selected.append(fused)
    return aggregate_backend(rows, evidence_kind), aggregate_backend(selected, evidence_kind, selected_set=True)


def _measure_blob(corpus, profile, vectors, repeats):
    index = BlobIndex(corpus.db, profile)
    start = perf_counter_ns()
    for stored, vector in zip(corpus.facts.values(), vectors[:42], strict=True):
        index.put(stored, vector)
    ingest_ns = perf_counter_ns() - start
    queries = {case.query_id: vector for case, vector in zip(corpus.cases, vectors[42:], strict=True)}
    component, selected = measure_offline(
        corpus,
        lambda case: blob_rank(index, corpus.scopes[case.query_id], queries[case.query_id]),
        profile.evidence_kind,
        repeats,
        channel="local_embedding",
    )
    component["profile"] = asdict(profile)
    component["local_vector_ingestion_ns"] = ingest_ns
    component["configuration"] = {
        "eligible_scan": "entire authorized corpus",
        "candidate_limit": None,
        "normalization": "unit norm before float32 packing",
        "score": "exact cosine",
        "tie_break": "cosine descending, memory_id ascending",
        "query_embedding": "already acquired complete cache; excluded from cached local timing",
    }
    return component, selected


def _observe_hindsight(corpus, study):
    rows, selected = [], []
    for case in corpus.cases:
        rank = study["rankings"][case.query_id]
        row, final = observe_component(corpus, case, rank)
        row["timing_ns"]["observed_component_total"] = sum(rank.timing_ns.values()) + final.timing_ns
        rows.append(row)
        if rank.status == "available":
            fused = observe_fusion(corpus, case, final, documents=rank.details["admitted_document_ids"])
            fused["timing_ns"]["observed_pipeline_total"] = (
                row["timing_ns"]["observed_component_total"] + fused["timing_ns"]["fts_read_and_production_validation"]
            )
        else:
            fused = observe_fusion(corpus, case)
            fused.update(status="failed", reason=rank.reason, semantic_failure_fts_fallback=True)
        selected.append(fused)
    component = aggregate_backend(rows, "genuine_model_retrieval")
    fused_report = aggregate_backend(selected, "genuine_model_retrieval", selected_set=True)
    component["status"] = study["status"]
    component["study"] = {key: value for key, value in study.items() if key not in {"rankings", "retained_documents"}}
    observed = [row for row in rows if row["timing_ns"].get("hindsight_recall")]
    timely = [
        row["query_id"]
        for row in observed
        if row["status"] == "available" and row["timing_ns"]["hindsight_recall"] <= 1_500_000_000
    ]
    component["foreground_budget_observation"] = {
        "production_budget_ms": 1500,
        "study_request_timeout_ms": 10000,
        "expected_query_count": 24,
        "attempted_query_count": len(observed),
        "successful_http_calls_within_budget": len(timely),
        "successful_http_query_ids_within_budget": timely,
        "boundary": (
            "HTTP completion only; production scheduling, admission, local validation and 30s cooldown excluded"
        ),
        "retrospective_projection": "not reconstructed; only observed timely HTTP count is reported",
    }
    return component, fused_report


def build_report(
    corpus, identity, *, selected_backends, repeats=10, embeddings=None, embedding_study=None, hindsight_study=None
):
    backends, selected_sets = {}, {}
    curated, genuine = "curated_synthetic_retrieval", "genuine_model_retrieval"
    if "fts" in selected_backends:
        backends["fts"], selected_sets["fts_only"] = measure_offline(
            corpus, lambda case: fts_rank(corpus.db, corpus.scopes[case.query_id], case.text), curated, repeats
        )
        backends["fts"]["configuration"] = {
            "query_builder": "production search_fact_ids; no added aliases/stemming",
            "rank_limit": 48,
            "explicit_assertion_fallback_limit": 6,
            "explicit_assertions_in_fixed_corpus": 0,
            "tie_break": "BM25 ascending, importance descending, memory_id descending",
        }
    else:
        backends["fts"] = unavailable(corpus, curated, reason="not_selected")
        selected_sets["fts_only"] = unavailable(corpus, curated, reason="not_selected", selected_set=True)
    if "blob" in selected_backends:
        hand_profile, hand = hand_vectors(corpus)
        backends["blob_contract"], selected_sets["fts_plus_blob_contract"] = _measure_blob(
            corpus, hand_profile, hand, repeats
        )
        backends["blob_contract"]["vector_fixture_sha256"] = sha256(HAND_VECTORS.read_bytes())
    if embeddings is not None:
        artifact, profile, vectors = embeddings
        backend_name = "blob" if profile.evidence_kind == genuine else "blob_cached_contract"
        backends[backend_name], selected_sets["fts_plus_" + backend_name] = _measure_blob(
            corpus, profile, vectors, repeats
        )
        backends[backend_name]["acquisition"] = artifact["acquisition"]
        backends[backend_name]["artifact_source_identity"] = artifact["source_identity"]
        backends[backend_name]["artifact_sha256"] = artifact["validated_file_sha256"]
    blob_status = "failed" if embedding_study and embedding_study["status"] == "failed" else "skipped"
    if "blob" not in backends:
        reason = (
            embedding_study.get("reason", "capability_or_budget_unverified")
            if embedding_study
            else "capability_or_budget_unverified"
        )
        if "blob" not in selected_backends:
            reason = "not_selected"
        backends["blob"] = unavailable(corpus, genuine, status=blob_status, reason=reason)
        selected_sets["fts_plus_blob"] = unavailable(
            corpus, genuine, status=blob_status, reason=reason, selected_set=True
        )
    if hindsight_study is not None:
        backends["hindsight"], selected_sets["fts_plus_hindsight"] = _observe_hindsight(corpus, hindsight_study)
    else:
        reason = "capability_or_budget_unverified" if "hindsight" in selected_backends else "not_selected"
        backends["hindsight"] = unavailable(corpus, genuine, reason=reason)
        selected_sets["fts_plus_hindsight"] = unavailable(corpus, genuine, reason=reason, selected_set=True)
    forbidden = sum(report["forbidden_admitted_count"] for report in (*backends.values(), *selected_sets.values()))
    max_selected = max(
        (row.get("selected_count", 0) for result in selected_sets.values() for row in result["queries"]), default=0
    )
    missing_vectors = sum(
        row.get("details", {}).get("missing_or_stale_vectors", 0)
        for result in backends.values()
        for row in result["queries"]
    )
    requests = list(embedding_study.get("requests", [])) if embedding_study else []
    if hindsight_study:
        requests += hindsight_study["manifest"]["requests"]
    return {
        "schema_version": 1,
        "source_identity": identity,
        "runtime": runtime_identity(),
        "measurement_status": "development_unfrozen" if identity["dirty"] else "frozen_source",
        "corpus": corpus_identity(corpus),
        "backends": backends,
        "selected_sets": selected_sets,
        "paired_component_differences": paired_differences(backends),
        "paired_selected_set_differences": paired_differences(selected_sets, selected_set=True),
        "embedding_acquisition": {key: value for key, value in (embedding_study or {}).items() if key != "artifact"},
        "invariants": {
            "passed": forbidden == 0 and max_selected <= 6 and missing_vectors == 0,
            "forbidden_admitted_count": forbidden,
            "largest_production_selected_set": max_selected,
            "missing_or_stale_vectors": missing_vectors,
            "quality_is_not_an_invariant_gate": True,
        },
        "network": {
            "requests": len(requests),
            "observations": requests,
            "default_policy": "zero HTTP; native and provider guards remain installed",
        },
        "timing": {
            "clock": "perf_counter_ns",
            "offline_warmups_per_query": 1,
            "offline_repetitions_per_query": repeats,
            "percentile": "nearest rank",
            "live_repetitions_per_query": 1,
            "live_sample_caveat": "24 fixed cases, not a latency SLA estimate",
            "embedding_query_latency": "unmeasured individually; acquisition batches mix facts and queries",
            "uncached_embedding_foreground_projection": "not supported by amortized batch acquisition",
        },
        "storage": sqlite_footprint(corpus.db),
        "methodology": {
            "production_block_order": ["recall_or_local_embedding", "episodic", "summary", "scene"],
            "quality_boundary": "component ranks and production-selected sets are separate; no fused global MRR",
            "no_answer_boundary": "candidate-return policy on two cases, not hallucination or answer quality",
            "scope_boundary": (
                "Hindsight ranks a session-filtered pool before audience/history postfiltering; FTS/BLOB prefilter"
            ),
            "panel_limits": (
                "26 distinct short summaries, 20 distinct query texts, equal importance 0.9, narrow alternate positives"
            ),
            "production_change": "none; facts-only Hindsight remains the production semantic backend",
            "external_cost_boundary": (
                "loopback relay can invoke paid upstream inference; "
                "frontend caps do not exactly bound internal LLM expense"
            ),
            "resource_boundary": (
                "Python traced allocations and isolated SQLite storage, not RSS or reclaimed Hindsight service RAM"
            ),
        },
    }
