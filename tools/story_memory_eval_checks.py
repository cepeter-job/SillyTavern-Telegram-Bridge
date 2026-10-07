"""Offline remote-boundary assertions, queued cleanup execution and measured counters."""

import math
import statistics
from types import SimpleNamespace

from story_memory_eval_support import CHAT


def facts_only_boundary(runtime, score):
    db, transport = runtime.db, runtime.transport
    complete_sources = True
    for row_id, content in db.execute("SELECT id,content FROM messages WHERE session_id='main'").fetchall():
        parts = db.execute(
            "SELECT start_offset,end_offset FROM memory_segments WHERE layer='episodes' "
            "AND valid=1 AND start_id=? ORDER BY start_offset",
            (row_id,),
        ).fetchall()
        offset = 0
        for start, end in parts:
            complete_sources = complete_sources and start == offset
            offset = end
        complete_sources = complete_sources and bool(parts) and offset == len(content)
    before = len(transport.calls)
    idle = runtime.drain("hindsight")
    cases = {case["case_id"]: case for case in score.cases}
    score.case(
        "facts-only.remote-boundary",
        "facts-only",
        {
            "only_native_fact_retains": bool(transport.native_payloads)
            and all(
                value["category"] == "native_fact" and "native-fact" in value["tags"]
                for value in transport.native_payloads
            ),
            "exact_local_summaries": all(value["locally_accepted"] for value in transport.native_payloads),
            "no_raw_mapping": not db.execute(
                "SELECT 1 FROM hindsight_documents WHERE kind='source_segment'"
            ).fetchone(),
            "no_raw_source": not db.execute("SELECT 1 FROM memory_segments WHERE layer='hindsight'").fetchone(),
            "complete_local_source_parts": complete_sources,
            "distant_and_alias_native_recall": all(
                case["passed"] for case in score.cases if case["category"] == "distant"
            )
            and cases["semantic.alias"]["passed"],
            "no_pending_native_index": not db.execute(
                "SELECT 1 FROM memory_fact_index WHERE state='pending'"
            ).fetchone(),
            "extra_drain_is_idle": idle == ["idle"] and len(transport.calls) == before,
        },
        retained_payload_count=len(transport.native_payloads),
    )


def drain_cleanup(runtime):
    from bridge.memory_workers import dispatch_memory_backlog
    from bridge.sqlite_store import db_connect

    # This fixture advances only cleanup workers, leaving unrelated derived work durable.
    runtime.db.execute("UPDATE memory_jobs SET next_attempt_at=9999999999")
    runtime.db.commit()
    submitted = []
    services = SimpleNamespace(
        config=runtime.settings,
        db_factory=lambda: db_connect(app_settings=runtime.settings),
        background=SimpleNamespace(submit=lambda *args: submitted.append(args) or True),
    )
    for _ in range(200):
        if not dispatch_memory_backlog(services, runtime.db):
            return
        _, callback, *args = submitted.pop()
        if callback.__name__ == "_memory_worker":
            raise AssertionError("Cleanup fixture dispatched unrelated memory work")
        callback(*args)
    raise AssertionError("Unbounded cleanup fixture")


def purge_and_drain(runtime):
    from bridge.memory_fact_store import index_fact_is_current
    from bridge.sqlite_store import write_transaction

    documents = [row[0] for row in runtime.db.execute("SELECT document_id FROM memory_fact_index")]
    calls = len(runtime.transport.calls)
    with write_transaction(runtime.db):
        runtime.memory.queue_cleanup(runtime.db, CHAT, "main")
    denied = len(runtime.transport.calls) == calls and all(
        index_fact_is_current(runtime.db, document_id) is None for document_id in documents
    )
    before = len(runtime.transport.documents_by_id)
    drain_cleanup(runtime)
    return before - len(runtime.transport.documents_by_id), denied


def measurement_metrics(runtime, repeats, samples, setup_ns, ingest_ns, drain_ns, heap_peak, page_bytes, wal_bytes):
    return {
        "setup_elapsed_ms": setup_ns / 1_000_000,
        "ingest_elapsed_ms": ingest_ns / 1_000_000,
        "drain_elapsed_ms": drain_ns / 1_000_000,
        "query_elapsed_ms_median": statistics.median(samples),
        "query_elapsed_ms_p95": sorted(samples)[math.ceil(0.95 * len(samples)) - 1],
        "query_sample_count": repeats,
        "query_warmups": 1,
        "python_tracemalloc_peak_bytes": heap_peak,
        "process_rss_bytes": None,
        "sqlite_page_bytes": page_bytes,
        "sqlite_wal_bytes": wal_bytes,
        "stub_request_count": len(runtime.transport.calls),
        "stub_request_max_bytes": max(x["bytes"] for x in runtime.transport.calls),
        "stub_calls_by_kind": {
            kind: sum(x["kind"] == kind for x in runtime.transport.calls)
            for kind in ["provider", "retain", "recall", "delete"]
        },
    }
