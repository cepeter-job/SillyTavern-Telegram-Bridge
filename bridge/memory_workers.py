"""Bounded durable memory dispatch and current-input execution."""

from __future__ import annotations

import logging
import time
import uuid

from bridge import memory_backend
from bridge.card_content import card_fields_from_file
from bridge.episodic_extraction import extract_episodic_memories_result
from bridge.memory_discovery import cleanup_candidate
from bridge.memory_fact_worker import run_fact_index as _run_fact_index
from bridge.memory_retirement_store import (
    RETIREMENT_SCOPE_UNPAUSED,
    accept_discovery_page,
    claim_discovery,
    defer_discovery,
    prepare_discovery_call,
    release_discovery,
)
from bridge.memory_store import (
    acknowledge_job,
    claim_is_current,
    claim_jobs,
    fail_job,
    next_source_segment,
    reconcile_archival_attempts,
    recover_expired_jobs,
    source_is_valid,
)
from bridge.model_router import ModelRoutingError
from bridge.narrative_repository import load_narrative_clock
from bridge.sqlite_store import write_transaction

MAX_SEGMENTS_PER_RUN = 8
MAX_ACTIVE_CLAIMS = 1  # matches the single memory executor; queued leases cannot expire waiting


def _claim_current(db, claim):
    return claim_is_current(db, claim)


def run_memory_claim(db, claim, session, fields, *, provider_port=None, app_settings):
    if db.in_transaction:
        raise RuntimeError("Memory workers require a committed source")
    result = "work_failed"
    try:
        if not _claim_current(db, claim):
            fail_job(db, claim, "stale_source")
            return "stale_source"
        initial_clock = load_narrative_clock(db, claim.chat_id, claim.session_id)
        initial_epoch = memory_backend._memory_hindsight_epoch(db, claim.chat_id, claim.session_id)
        if claim.layer in {"hindsight", "curator"} and memory_backend.memory_mode(db, claim.chat_id) != "on":
            result = "disabled"
        elif claim.layer in {"hindsight", "episodes"}:
            if claim.layer == "hindsight":
                if not memory_backend.cleanup_retired_memory_documents(
                    db, claim.chat_id, claim.session_id, app_settings=app_settings, blocking_only=True
                ):
                    fail_job(db, claim, "work_failed")
                    return "work_failed"
            fact_calls = 0
            if claim.layer == "hindsight":
                result, fact_calls = _run_fact_index(db, claim, fields, app_settings=app_settings)
                if result != "complete":
                    fail_job(db, claim, result)
                    return result
            for _ in range(MAX_SEGMENTS_PER_RUN - fact_calls):
                source = next_source_segment(
                    db, claim.chat_id, claim.session_id, claim.layer, through_id=claim.target_id
                )
                if source is None:
                    result = "complete"
                    break
                with write_transaction(db):
                    db.execute(
                        "UPDATE memory_jobs SET lease_deadline=? WHERE lease_token=?",
                        (time.time() + 900, claim.token),
                    )
                if not _claim_current(db, claim) or not source_is_valid(db, source):
                    result = "stale_source"
                    break
                if claim.layer == "hindsight":
                    # Recheck mode at the external-call boundary.
                    if memory_backend.memory_mode(db, claim.chat_id) != "on":
                        result = "disabled"
                        break
                    result = memory_backend.retain_archival_source(
                        db,
                        source,
                        fields.get("name", "Story"),
                        f"Archival transcript part: {source.role}; source={source.start_id}; "
                        f"offsets={source.start_offset}:{source.end_offset}",
                        app_settings=app_settings,
                        claim=claim,
                    )
                    if result != "complete":
                        break
                else:
                    extraction = extract_episodic_memories_result(
                        db,
                        claim.chat_id,
                        session,
                        source_text=source.content,
                        source_start_rowid=source.start_id,
                        source_end_rowid=source.end_id,
                        provider_port=provider_port,
                        app_settings=app_settings,
                        source_valid=lambda captured=source: (
                            _claim_current(db, claim) and source_is_valid(db, captured)
                        ),
                        source_ref=source,
                    )
                    if extraction.status != "complete":
                        result = "stale_source"
                        break
            else:
                result = (
                    "complete"
                    if next_source_segment(db, claim.chat_id, claim.session_id, claim.layer, through_id=claim.target_id)
                    is None
                    else "deferred"
                )
            if (
                claim.layer == "hindsight"
                and result == "complete"
                and db.execute(
                    "SELECT 1 FROM memory_fact_index WHERE chat_id=? AND session_id=? AND session_created_at=? "
                    "AND state='pending' LIMIT 1",
                    (claim.chat_id, claim.session_id, claim.session_created_at),
                ).fetchone()
            ):
                result = "deferred"
        else:
            result = _run_derived_layer(
                db, claim, session, fields, provider_port=provider_port, app_settings=app_settings
            )
        if result == "complete":
            # A rewrite during a derived call must not ACK old captured work.
            with write_transaction(db):
                current_clock = load_narrative_clock(db, claim.chat_id, claim.session_id)
                if (
                    not _claim_current(db, claim)
                    or initial_clock is None
                    or current_clock is None
                    or initial_clock["rewrite_revision"] != current_clock["rewrite_revision"]
                    or (
                        claim.layer in {"hindsight", "curator"}
                        and initial_epoch != memory_backend._memory_hindsight_epoch(db, claim.chat_id, claim.session_id)
                    )
                ):
                    fail_job(db, claim, "stale_source")
                    return "stale_source"
                if acknowledge_job(db, claim):
                    return result
            result = "stale_source"
    except ModelRoutingError:
        logging.warning("Durable memory layer %s blocked by model configuration", claim.layer, exc_info=True)
        result = "configuration"
    except Exception:
        logging.warning("Durable memory layer %s failed", claim.layer, exc_info=True)
        result = "work_failed"
    fail_job(db, claim, result, deferred=result == "deferred")
    return result


def _run_derived_layer(db, claim, session, fields, *, provider_port, app_settings):
    from bridge.memory_derived import run_derived_layer

    return run_derived_layer(
        db,
        claim,
        session,
        fields,
        provider_port=provider_port,
        app_settings=app_settings,
        valid=lambda: (
            _claim_current(db, claim)
            and (claim.layer != "curator" or memory_backend.memory_mode(db, claim.chat_id) == "on")
        ),
        max_parts=MAX_SEGMENTS_PER_RUN,
    )


def _memory_worker(services, claim):
    db = services.db_factory()
    try:
        if not _claim_current(db, claim):
            fail_job(db, claim, "stale_source")
            return
        session = services.session.load(db, claim.chat_id, claim.session_id, services.config.default_model)
        fields = card_fields_from_file(session["character_file"], app_settings=services.config)
        run_memory_claim(db, claim, session, fields, provider_port=services.provider, app_settings=services.config)
    except Exception:
        logging.warning("Could not reconstruct durable memory work", exc_info=True)
        fail_job(db, claim)
    finally:
        db.close()


def _retired_memory_worker(services, chat_id, session_id, token):
    db = services.db_factory()
    try:
        memory_backend.cleanup_retired_memory_documents(
            db,
            chat_id,
            session_id,
            app_settings=services.config,
            lease_token=token,
        )
    finally:
        with write_transaction(db):
            db.execute(
                "UPDATE memory_retired_documents SET lease_token='',lease_deadline=0 WHERE lease_token=?",
                (token,),
            )
        db.close()


def _dispatch_retirement(services, db, *, allow_watches=True):
    now = time.time()
    token = uuid.uuid4().hex
    with write_transaction(db):
        row = db.execute(
            "SELECT chat_id,session_id FROM memory_retired_documents r WHERE deleted=0 AND lease_token='' "  # noqa: S608 -- fixed internal SQL predicate; values are bound
            "AND next_attempt_at<=? "
            "AND (? OR NOT EXISTS(SELECT 1 FROM memory_archival_attempts a "
            "WHERE a.document_id=r.document_id AND a.finished=0)) "
            "AND " + RETIREMENT_SCOPE_UNPAUSED + " ORDER BY next_attempt_at,attempts,chat_id,session_id LIMIT 1",
            (now, int(allow_watches), now),
        ).fetchone()
        if row is None:
            return None
        db.execute(
            "UPDATE memory_retired_documents SET lease_token=?,lease_deadline=? WHERE rowid IN ("
            "SELECT rowid FROM memory_retired_documents r WHERE chat_id=? AND session_id=? AND deleted=0 "
            "AND lease_token='' AND next_attempt_at<=? AND (? OR NOT EXISTS("
            "SELECT 1 FROM memory_archival_attempts a WHERE a.document_id=r.document_id AND a.finished=0)) "
            "ORDER BY document_id LIMIT 16)",
            (token, now + 900, *row, now, int(allow_watches)),
        )
        if db.execute(
            "SELECT 1 FROM memory_retired_documents r JOIN memory_archival_attempts a "
            "ON a.document_id=r.document_id WHERE r.lease_token=? AND a.finished=0 LIMIT 1",
            (token,),
        ).fetchone():
            db.execute(
                "INSERT INTO meta(key,value) VALUES('memory_retirement_turn','normal') "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
            )
    if services.background.submit("hindsight_retain", _retired_memory_worker, services, row[0], row[1], token):
        return 1
    with write_transaction(db):
        db.execute(
            "UPDATE memory_retired_documents SET lease_token='',lease_deadline=0,next_attempt_at=? WHERE lease_token=?",
            (now + 5, token),
        )
    return 0


def _legacy_discovery_worker(services, request_id: str, lease_token: str) -> None:
    db = services.db_factory()
    try:
        for _ in range(2):
            captured = prepare_discovery_call(db, request_id, lease_token)
            if captured is None:
                break
            result = memory_backend.list_hindsight_cleanup_page(
                captured["chat_id"],
                captured["session_id"],
                captured["query_kind"],
                captured["offset"],
                app_settings=services.config,
            )
            items = getattr(result, "items", None)
            if items is None:
                raise ValueError("Document list omitted its items")
            items = list(items)
            targets, unknown = [], False
            for item in items:
                document_id, eligible = cleanup_candidate(item, captured["session_id"], captured["generation_tag"])
                unknown |= eligible is None
                if eligible:
                    targets.append(document_id)
            if not accept_discovery_page(
                db,
                request_id,
                lease_token,
                captured,
                targets,
                unknown=unknown,
                count=len(items),
                total=int(getattr(result, "total", 0) or 0),
            ):
                break
    except Exception:
        logging.warning("Legacy memory discovery deferred", exc_info=True)
        with write_transaction(db):
            defer_discovery(db, request_id, lease_token)
    finally:
        with write_transaction(db):
            release_discovery(db, request_id, lease_token)
        db.close()


def _dispatch_legacy_discovery(services, db) -> int | None:
    claimed = claim_discovery(db)
    if claimed is None:
        return None
    request_id, token = claimed
    with write_transaction(db):
        db.execute(
            "INSERT INTO meta(key,value) VALUES('memory_retirement_turn','normal') ON CONFLICT(key) DO "
            "UPDATE SET value=excluded.value"
        )
    if services.background.submit("hindsight_retain", _legacy_discovery_worker, services, request_id, token):
        return 1
    with write_transaction(db):
        defer_discovery(db, request_id, token, delay=5)
        release_discovery(db, request_id, token)
    return 0


def dispatch_memory_backlog(services, db, *, startup=False):
    if startup:
        # One runtime owns the process; startup recovers interrupted claims.
        with write_transaction(db):
            db.execute("UPDATE memory_jobs SET lease_token='',lease_deadline=0 WHERE lease_token<>''")
    else:
        recover_expired_jobs(db)
    try:
        reconcile_archival_attempts(db)
    except Exception:
        logging.warning("Raw archival recovery deferred", exc_info=True)
        return 0
    with write_transaction(db):
        db.execute(
            "UPDATE memory_retired_documents SET lease_token='',lease_deadline=0 "
            "WHERE lease_token<>'' AND (? OR lease_deadline<=?)",
            (int(startup), time.time()),
        )
    with write_transaction(db):
        db.execute(
            "UPDATE memory_cleanup_discovery SET lease_token='',lease_deadline=0 WHERE lease_token<>'' "
            "AND (? OR lease_deadline<=?)",
            (int(startup), time.time()),
        )
    discovery_active = db.execute("SELECT 1 FROM memory_cleanup_discovery WHERE lease_token<>'' LIMIT 1").fetchone()
    active = db.execute("SELECT count(*) FROM memory_jobs WHERE lease_token<>''").fetchone()[0]
    retired_active = db.execute("SELECT 1 FROM memory_retired_documents WHERE lease_token<>'' LIMIT 1").fetchone()
    if active >= MAX_ACTIVE_CLAIMS or retired_active or discovery_active:
        return 0
    normal_turn = (
        db.execute("SELECT 1 FROM meta WHERE key='memory_retirement_turn' AND value='normal'").fetchone() is not None
    )
    # Recurring uncertainty cannot monopolize the retirement-first dispatcher.
    # Finite retirements retain priority; watches alternate with due normal work.
    retirement = _dispatch_retirement(services, db, allow_watches=not normal_turn)
    if retirement is not None:
        return retirement
    if not normal_turn:
        discovery = _dispatch_legacy_discovery(services, db)
        if discovery is not None:
            return discovery
    enabled = db.execute(
        "SELECT DISTINCT layer FROM memory_jobs WHERE layer NOT IN ('hindsight','curator') OR "
        "COALESCE((SELECT value FROM meta WHERE key='memory_mode:' || memory_jobs.chat_id),'on')='on'"
    ).fetchall()
    claims = claim_jobs(db, layers=tuple(row[0] for row in enabled), limit=MAX_ACTIVE_CLAIMS - active, autonomous=True)
    if claims:
        with write_transaction(db):
            db.execute(
                "INSERT INTO meta(key,value) VALUES('memory_retirement_turn','retirement') "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
            )
    dispatched = 0
    for claim in claims:
        if claim.layer in {"hindsight", "curator"} and memory_backend.memory_mode(db, claim.chat_id) != "on":
            fail_job(db, claim, "disabled")
            continue
        if services.background.submit("hindsight_retain", _memory_worker, services, claim):
            dispatched += 1
        else:
            fail_job(db, claim, "executor_rejected")
    if normal_turn and not claims:
        retirement = _dispatch_retirement(services, db)
        return retirement if retirement is not None else (_dispatch_legacy_discovery(services, db) or 0)
    return dispatched
