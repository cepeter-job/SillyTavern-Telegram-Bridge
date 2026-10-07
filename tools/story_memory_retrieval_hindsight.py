"""Owned-bank Hindsight HTTP study with production local indexing and final authority."""

from __future__ import annotations

import importlib.metadata
import re
from pathlib import Path
from time import perf_counter_ns
from unittest.mock import patch

from story_memory_retrieval_fixture import require
from story_memory_retrieval_http import BoundedHTTP, StudyHTTPError
from story_memory_retrieval_owned_bank import OwnedBank
from story_memory_retrieval_rank import CandidateRanking, candidate_ranking, fact_fingerprint

from bridge.hindsight_recall_runtime import RECALL_MAX_RESULTS
from bridge.memory_fact_store import index_fact_is_current
from bridge.memory_scope_store import eligible_fact


def hindsight_client(endpoint: str, request_budget: int, *, token: str = "") -> BoundedHTTP:
    require(33 <= request_budget <= 38, "Fixed Hindsight study requires an explicit 33–38 request budget")
    return BoundedHTTP(
        endpoint,
        request_budget=request_budget,
        phase_limits={"absence": 1, "create": 1, "config": 1, "retain": 4, "recall": 24, "readiness": 4, "cleanup": 3},
        deadline_seconds=300,
        token=token,
        cleanup_reserve=2,
        cleanup_reserve_seconds=20,
    )


def _documents(corpus, tag):
    items, mapping = [], {}
    for stored in corpus.facts.values():
        rows = corpus.db.execute(
            "SELECT document_id FROM memory_fact_index WHERE memory_id=? AND state='pending'", (stored.memory_id,)
        ).fetchall()
        current = [row[0] for row in rows if index_fact_is_current(corpus.db, row[0]) == stored]
        require(len(current) == 1, "Expected one current native document per canonical fact")
        document = current[0]
        mapping[document] = stored
        items.append(
            {
                "document_id": document,
                "content": stored.fact.summary,
                "tags": [tag, "session:" + stored.session_id, "native-fact"],
            }
        )
    return items, mapping


def _acknowledge_receipts(corpus, bank, mapping):
    """Replay verified synchronous batch receipts through the actual local index worker."""
    from bridge import memory_backend

    receipts = {doc for batch in bank.manifest["retained_batches"] for doc in batch["document_ids"]}
    require(receipts == set(mapping), "Incomplete retains cannot establish production fused identity")
    consumed = set()

    def retained(chat_id, session_id, document_id, character, content, *args, **kwargs):
        stored = mapping.get(document_id)
        require(
            stored is not None
            and document_id in receipts
            and content == stored.fact.summary
            and (chat_id, session_id) == (stored.chat_id, stored.session_id)
            and index_fact_is_current(corpus.db, document_id) == stored,
            "Receipt no longer matches canonical authority",
        )
        consumed.add(document_id)
        return True

    with patch.object(memory_backend, "_retain_with_client", retained):
        # This worker is facts-only on the merged base; the seam consumes receipts, never HTTP.
        for session in corpus.sessions.values():
            corpus.runtime.drain("hindsight", session=session)
    require(consumed == receipts, "Production index did not acknowledge all verified receipts")


def _recall(corpus, client, bank, mapping, case):
    scope = corpus.scopes[case.query_id]
    tags = [bank.synthetic_tag, "session:" + scope.session_id, "native-fact"]
    response = client.request(
        "POST",
        bank.route + "/memories/recall",
        {
            "query": case.text,
            "types": ["world", "experience"],
            "tags": tags,
            "tags_match": "all_strict",
            "budget": "low",
            "max_tokens": 1024,
            "prefer_observations": False,
            "trace": False,
        },
        phase="recall",
    )
    records = (response.data or {}).get("results")
    require(isinstance(records, list) and len(records) <= 256, "Invalid/beyond-cap recall response")
    started = perf_counter_ns()
    # Keep bounded diagnostics distinct from the production raw-record admission window.
    raw = [
        record["id"]
        for record in records
        if isinstance(record, dict)
        and isinstance(record.get("id"), str)
        and re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", record["id"])
    ]
    mapped, admitted, documents = [], [], []
    seen = set()
    admitted_records = records[:RECALL_MAX_RESULTS]
    for record in admitted_records:
        require(isinstance(record, dict), "Invalid recall record")
        document = record.get("document_id")
        if not isinstance(document, str) or document not in mapping:
            continue
        stored = mapping[document]
        mapped.append(stored.memory_id)
        returned_tags = record.get("tags")
        require(
            isinstance(returned_tags, list) and all(isinstance(tag, str) for tag in returned_tags),
            "Invalid recall tags",
        )
        if record.get("type") not in {"world", "experience"} or not set(tags) <= set(returned_tags):
            continue
        current = eligible_fact(corpus.db, scope, stored.memory_id)
        if current is None or fact_fingerprint(current) != fact_fingerprint(stored) or stored.memory_id in seen:
            continue
        if index_fact_is_current(corpus.db, document) != stored:
            continue
        admitted.append(current)
        documents.append(document)
        seen.add(stored.memory_id)
    return candidate_ranking(
        "hindsight",
        admitted,
        "genuine_model_retrieval",
        raw_candidate_ids=tuple(raw),
        timing_ns={"hindsight_recall": response.elapsed_ns, "candidate_eligibility": perf_counter_ns() - started},
        details={
            "candidate_count": len(records),
            "raw_record_count": len(records),
            "production_raw_record_limit": RECALL_MAX_RESULTS,
            "admission_record_count": len(admitted_records),
            "truncated_record_count": len(records) - len(admitted_records),
            "raw_canonical_ids": mapped,
            "admitted_document_ids": documents,
            "rejected_candidate_count": len(records) - len(admitted),
        },
    )


def run_hindsight_study(corpus, client, manifest_path: Path, *, source_identity: dict) -> dict:
    require(source_identity.get("dirty") is False, "Live Hindsight requires frozen clean source")
    bank = OwnedBank(client, corpus.fixture_sha256, manifest_path, source_identity)
    rankings, mapping = {}, {}
    result = {
        "status": "failed",
        "rankings": rankings,
        "retained_documents": {},
        "manifest": bank.manifest,
        "http_contract_version": "0.10.2",
        "sdk_version": importlib.metadata.version("hindsight-client"),
        "sdk_used": False,
        "endpoint": client.identity,
        "reason": "",
    }
    started = perf_counter_ns()
    try:
        items, mapping = _documents(corpus, bank.synthetic_tag)
        bank.bind_documents(items)
        bank.open()
        for offset in range(0, len(items), 12):
            bank.retain(items[offset : offset + 12])
        _acknowledge_receipts(corpus, bank, mapping)
        result["retained_documents"] = {document: stored.memory_id for document, stored in mapping.items()}
        result["ingestion_ns"] = perf_counter_ns() - started
        bank.manifest["state"] = "recalling"
        bank.save()
        for case in corpus.cases:
            observations_before = len(client.observations)
            try:
                rankings[case.query_id] = _recall(corpus, client, bank, mapping, case)
            except (StudyHTTPError, ValueError, KeyError, TypeError) as exc:
                reason = exc.category if isinstance(exc, StudyHTTPError) else "invalid_recall"
                timings = (
                    {"hindsight_recall": client.observations[-1]["elapsed_ns"]}
                    if len(client.observations) > observations_before
                    else {}
                )
                rankings[case.query_id] = CandidateRanking(
                    "hindsight",
                    (),
                    "genuine_model_retrieval",
                    status="failed",
                    reason=reason,
                    timing_ns=timings,
                    details={"attempted": bool(timings)},
                )
                bank.manifest["errors"].append({"phase": "recall", "query_id": case.query_id, "category": reason})
    except (StudyHTTPError, ValueError, KeyError, TypeError) as exc:
        result["reason"] = exc.category if isinstance(exc, StudyHTTPError) else "owned_bank_protocol_failed"
        bank.manifest["errors"].append({"phase": bank.manifest["state"], "category": result["reason"]})
    finally:
        bank.cleanup()
    for case in corpus.cases:
        rankings.setdefault(
            case.query_id,
            CandidateRanking(
                "hindsight",
                (),
                "genuine_model_retrieval",
                status="failed",
                reason="ingestion_or_setup_incomplete",
                details={"attempted": False},
            ),
        )
    if all(rank.status == "available" for rank in rankings.values()) and not bank.manifest["cleanup_pending"]:
        result["status"] = "available"
    result["elapsed_ns"] = perf_counter_ns() - started
    return result
