"""Bounded durable memory dispatch and current-input execution."""

from __future__ import annotations

import logging
import time
import uuid

from bridge import memory_backend
from bridge.card_content import card_fields_from_file
from bridge.episodic_extraction import extract_episodic_memories_result
from bridge.memory_fact_store import index_fact_is_current
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
from bridge.narrative_repository import load_narrative_clock
from bridge.sqlite_store import write_transaction

MAX_SEGMENTS_PER_RUN = 8
MAX_ACTIVE_CLAIMS = 1  # matches the single memory executor; queued leases cannot expire waiting


def _claim_current(db, claim):
    return claim_is_current(db, claim)


def _retire_fact_document(db, document_id):
    db.execute("UPDATE memory_fact_index SET state='retired' WHERE document_id=?", (document_id,))
    db.execute(
        "INSERT INTO memory_retired_documents(chat_id,session_id,document_id) "
        "SELECT chat_id,session_id,document_id FROM memory_fact_index WHERE document_id=? "
        "ON CONFLICT(chat_id,session_id,document_id) DO UPDATE SET deleted=0,next_attempt_at=0",
        (document_id,),
    )


def _run_fact_index(db, claim, fields, *, app_settings):
    """Reserve native-fact progress before raw archival work, within the shared call budget."""
    rows = db.execute(
        "SELECT document_id FROM memory_fact_index WHERE chat_id=? AND session_id=? "
        "AND session_created_at=? AND state='pending' ORDER BY created_at,document_id LIMIT 4",
        (claim.chat_id, claim.session_id, claim.session_created_at),
    ).fetchall()
    calls = 0
    for (document_id,) in rows:
        with memory_backend.hindsight_session_lock(claim.chat_id, claim.session_id):
            with write_transaction(db):
                if not _claim_current(db, claim):
                    return "stale_source", calls
                db.execute(
                    "UPDATE memory_jobs SET lease_deadline=? WHERE lease_token=?", (time.time() + 900, claim.token)
                )
                fact = index_fact_is_current(db, document_id)
                if fact is None:
                    _retire_fact_document(db, document_id)
                    continue
            if memory_backend.memory_mode(db, claim.chat_id) != "on":
                return "disabled", calls
            calls += 1
            retained = memory_backend._retain_with_client(
                claim.chat_id,
                claim.session_id,
                document_id,
                fields.get("name", "Story"),
                fact.fact.summary,
                "Locally accepted native story fact; audience is enforced by SQLite",
                "native_fact",
                "Hindsight native fact retain unavailable for chat %s",
                app_settings=app_settings,
            )
            with write_transaction(db):
                if index_fact_is_current(db, document_id) is None:
                    _retire_fact_document(db, document_id)
                    return "stale_source", calls
                if not _claim_current(db, claim):
                    return "stale_source", calls
                if memory_backend.memory_mode(db, claim.chat_id) != "on":
                    return "disabled", calls
                if not retained:
                    db.execute(
                        "UPDATE memory_fact_index SET last_error='retain_failed' WHERE document_id=?",
                        (document_id,),
                    )
                    return "retain_failed", calls
                db.execute(
                    "UPDATE memory_fact_index SET state='retained',last_error='' WHERE document_id=?",
                    (document_id,),
                )
                db.execute(
                    "INSERT OR REPLACE INTO hindsight_documents(chat_id,session_id,document_id,kind,created_at) "
                    "VALUES(?,?,?,'native_fact',?)",
                    (claim.chat_id, claim.session_id, document_id, time.time()),
                )
    return "complete", calls


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
                with memory_backend.hindsight_session_lock(claim.chat_id, claim.session_id):
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
                with memory_backend.hindsight_session_lock(claim.chat_id, claim.session_id):
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
            "SELECT chat_id,session_id FROM memory_retired_documents r WHERE deleted=0 AND lease_token='' "
            "AND next_attempt_at<=? AND COALESCE((SELECT value FROM meta "
            "WHERE key='memory_mode:'||r.chat_id),'on')='on' "
            "AND (? OR NOT EXISTS(SELECT 1 FROM memory_archival_attempts a "
            "WHERE a.document_id=r.document_id AND a.finished=0)) "
            "ORDER BY next_attempt_at,attempts,chat_id,session_id LIMIT 1",
            (now, int(allow_watches)),
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
    active = db.execute("SELECT count(*) FROM memory_jobs WHERE lease_token<>''").fetchone()[0]
    retired_active = db.execute("SELECT 1 FROM memory_retired_documents WHERE lease_token<>'' LIMIT 1").fetchone()
    if active >= MAX_ACTIVE_CLAIMS or retired_active:
        return 0
    normal_turn = (
        db.execute("SELECT 1 FROM meta WHERE key='memory_retirement_turn' AND value='normal'").fetchone() is not None
    )
    # Recurring uncertainty cannot monopolize the retirement-first dispatcher.
    # Finite retirements retain priority; watches alternate with due normal work.
    retirement = _dispatch_retirement(services, db, allow_watches=not normal_turn)
    if retirement is not None:
        return retirement
    enabled = db.execute(
        "SELECT DISTINCT layer FROM memory_jobs WHERE layer NOT IN ('hindsight','curator') OR "
        "COALESCE((SELECT value FROM meta WHERE key='memory_mode:' || memory_jobs.chat_id),'on')='on'"
    ).fetchall()
    claims = claim_jobs(db, layers=tuple(row[0] for row in enabled), limit=MAX_ACTIVE_CLAIMS - active)
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
        return _dispatch_retirement(services, db) or 0
    return dispatched
