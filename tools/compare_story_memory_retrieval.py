#!/usr/bin/env python3
"""Compare fixed synthetic story retrieval; default operation performs zero HTTP."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import tracemalloc
from pathlib import Path
from time import perf_counter_ns

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from story_memory_retrieval_artifacts import (  # noqa: E402
    load_embedding_artifact,
    save_artifact,
    validate_embedding_artifact,
)
from story_memory_retrieval_corpus import RetrievalCorpus  # noqa: E402
from story_memory_retrieval_fixture import require, sha256  # noqa: E402
from story_memory_retrieval_hindsight import hindsight_client, run_hindsight_study  # noqa: E402
from story_memory_retrieval_identity import source_identity  # noqa: E402
from story_memory_retrieval_live import embedding_client, fetch_embeddings  # noqa: E402
from story_memory_retrieval_report import build_report  # noqa: E402


def parse_options(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--backends", default="fts,blob,hindsight", help="Comma-separated: fts,blob,hindsight")
    parser.add_argument("--require-backends", default="", help="Make unavailable selected backends a nonzero result")
    parser.add_argument("--embedding-artifact", type=Path)
    parser.add_argument("--enable-live-embeddings", action="store_true")
    parser.add_argument("--embedding-endpoint")
    parser.add_argument("--embedding-model")
    parser.add_argument("--embedding-dimensions", type=int)
    parser.add_argument("--embedding-revision")
    parser.add_argument("--embedding-request-budget", type=int)
    parser.add_argument("--embedding-token-env", help="Environment variable name, never the credential value")
    parser.add_argument("--save-embedding-artifact", type=Path)
    parser.add_argument("--enable-live-hindsight", action="store_true")
    parser.add_argument("--hindsight-endpoint")
    parser.add_argument("--hindsight-request-budget", type=int)
    parser.add_argument("--hindsight-token-env", help="Environment variable name, never the credential value")
    parser.add_argument("--owned-bank-manifest", type=Path)
    args = parser.parse_args(argv)
    try:
        args.backends = set(args.backends.split(","))
        args.require_backends = set(filter(None, args.require_backends.split(",")))
        require(args.backends <= {"fts", "blob", "hindsight"}, "Unknown backend selection")
        require(args.require_backends <= args.backends, "Required backends must be selected")
        require(1 <= args.repeats <= 100, "Offline repeats must be between 1 and 100")
        embedding_values = [
            args.embedding_endpoint,
            args.embedding_model,
            args.embedding_dimensions,
            args.embedding_revision,
            args.embedding_request_budget,
            args.save_embedding_artifact,
        ]
        if args.enable_live_embeddings:
            require(
                all(embedding_values) and "blob" in args.backends,
                "Live embeddings require endpoint/model/dimensions/revision/budget/output and blob selection",
            )
            require(
                args.embedding_request_budget == 5 and 1 <= args.embedding_dimensions <= 8192,
                "Live embeddings require exactly five attempts and dimensions 1–8192",
            )
            require(not args.embedding_artifact, "Choose a cached artifact or explicit live acquisition")
            require(not args.save_embedding_artifact.exists(), "Embedding artifact output already exists")
        else:
            require(
                not any(embedding_values) and not args.embedding_token_env,
                "Embedding options require --enable-live-embeddings",
            )
        require(not args.embedding_artifact or "blob" in args.backends, "Cached embeddings require blob selection")
        if args.enable_live_hindsight:
            require(
                args.hindsight_endpoint
                and args.hindsight_request_budget
                and args.owned_bank_manifest
                and "hindsight" in args.backends,
                "Live Hindsight requires endpoint/budget/manifest and hindsight selection",
            )
            require(33 <= args.hindsight_request_budget <= 38, "Hindsight request budget must be 33–38")
            require(not args.owned_bank_manifest.exists(), "Owned-bank manifest already exists; it cannot be adopted")
        else:
            require(
                not any(
                    [
                        args.hindsight_endpoint,
                        args.hindsight_request_budget,
                        args.hindsight_token_env,
                        args.owned_bank_manifest,
                    ]
                ),
                "Hindsight options require --enable-live-hindsight",
            )
        paths = [
            path.resolve()
            for path in (args.output, args.embedding_artifact, args.save_embedding_artifact, args.owned_bank_manifest)
            if path
        ]
        require(len(paths) == len(set(paths)), "Report, cache and ownership manifest must have distinct paths")
        for name in (args.embedding_token_env, args.hindsight_token_env):
            require(not name or bool(os.environ.get(name)), "Named credential variable is missing or empty")
    except ValueError as exc:
        parser.error(str(exc))
    return args, parser


def _embeddings(corpus, args, client, identity):
    if args.embedding_artifact:
        try:
            artifact, profile, vectors = load_embedding_artifact(args.embedding_artifact, corpus)
            artifact["validated_file_sha256"] = sha256(args.embedding_artifact.read_bytes())
            return (artifact, profile, vectors), None
        except (OSError, ValueError, KeyError, TypeError):
            return None, {"status": "failed", "reason": "invalid_embedding_artifact", "requests": []}
    if client is None:
        return None, None
    study = fetch_embeddings(
        corpus,
        client,
        model=args.embedding_model,
        dimensions=args.embedding_dimensions,
        revision=args.embedding_revision,
        source_identity=identity,
    )
    if study["artifact"] is None:
        return None, study
    artifact = study["artifact"]
    profile, vectors = validate_embedding_artifact(artifact, corpus)
    save_artifact(args.save_embedding_artifact, artifact)
    artifact["validated_file_sha256"] = sha256(args.save_embedding_artifact.read_bytes())
    return (artifact, profile, vectors), study


def main(argv=None):
    args, parser = parse_options(argv)
    identity = source_identity()
    try:
        if args.enable_live_embeddings or args.enable_live_hindsight:
            require(not identity["dirty"], "Live studies require a committed clean source checkout")
        # Capture socket access before installing the isolated corpus guards.
        embedding_http = (
            embedding_client(
                args.embedding_endpoint,
                args.embedding_request_budget,
                token=os.environ.get(args.embedding_token_env, "") if args.embedding_token_env else "",
            )
            if args.enable_live_embeddings
            else None
        )
        hindsight_http = (
            hindsight_client(
                args.hindsight_endpoint,
                args.hindsight_request_budget,
                token=os.environ.get(args.hindsight_token_env, "") if args.hindsight_token_env else "",
            )
            if args.enable_live_hindsight
            else None
        )
    except ValueError as exc:
        parser.error(str(exc))
    start = perf_counter_ns()
    tracing_owned = not tracemalloc.is_tracing()
    if tracing_owned:
        tracemalloc.start()
    try:
        with tempfile.TemporaryDirectory(prefix="story-memory-comparison-") as temporary:
            with RetrievalCorpus(Path(temporary)) as corpus:
                corpus_setup_ns = perf_counter_ns() - start
                embeddings, embedding_study = _embeddings(corpus, args, embedding_http, identity)
                hindsight_study = (
                    run_hindsight_study(corpus, hindsight_http, args.owned_bank_manifest, source_identity=identity)
                    if hindsight_http is not None
                    else None
                )
                report = build_report(
                    corpus,
                    identity,
                    selected_backends=args.backends,
                    repeats=args.repeats,
                    embeddings=embeddings,
                    embedding_study=embedding_study,
                    hindsight_study=hindsight_study,
                )
                current, peak = tracemalloc.get_traced_memory()
                report["storage"]["python_tracemalloc"] = {
                    "current_bytes": current,
                    "peak_bytes": peak,
                    "started_by_study": tracing_owned,
                    "boundary": (
                        "traced Python allocations from corpus setup through measurements; "
                        "active during timing, not RSS"
                    ),
                }
                report["timing"].update(corpus_setup_ns=corpus_setup_ns, whole_study_ns=perf_counter_ns() - start)
        after = source_identity()
        source_unchanged = all(after[key] == identity[key] for key in ("revision", "source_sha256"))
        report["invariants"]["source_unchanged_during_run"] = source_unchanged
        report["invariants"]["passed"] &= source_unchanged
        required = sorted(name for name in args.require_backends if report["backends"][name]["status"] != "available")
        report["required_backend_failures"] = required
        failed = [name for name, backend in report["backends"].items() if backend["status"] == "failed"]
        report["status"] = "failed" if required or failed or not report["invariants"]["passed"] else "completed"
        save_artifact(args.output, report)
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "measurement_status": report["measurement_status"],
                    "source_revision": identity["revision"],
                    "backend_status": {name: backend["status"] for name, backend in report["backends"].items()},
                    "http_requests": report["network"]["requests"],
                }
            )
        )
        return 0 if report["status"] == "completed" else 1
    finally:
        if tracing_owned:
            tracemalloc.stop()


if __name__ == "__main__":
    raise SystemExit(main())
