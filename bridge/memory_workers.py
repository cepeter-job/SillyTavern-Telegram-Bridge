"""Bounded durable memory dispatch and current-input execution."""

from __future__ import annotations

import logging
import time

from bridge import memory_backend
from bridge.card_content import card_fields_from_file
from bridge.episodic_extraction import extract_episodic_memories_result
from bridge.memory_store import (
    acknowledge_job,
    claim_jobs,
    fail_job,
    next_source_segment,
    pending_memory_invalidation,
    recover_expired_jobs,
    source_is_valid,
    store_segment,
)
from bridge.narrative_repository import load_narrative_clock
from bridge.sqlite_store import write_transaction

MAX_SEGMENTS_PER_RUN = 8
MAX_ACTIVE_CLAIMS = 1  # matches the single memory executor; queued leases cannot expire waiting


def _claim_current(db, claim):
    return bool(
        db.execute(
            "SELECT 1 FROM memory_jobs j JOIN sessions s ON s.chat_id=j.chat_id AND s.session_id=j.session_id "
            "AND s.created_at=j.session_created_at JOIN memory_layer_state l ON l.chat_id=j.chat_id "
            "AND l.session_id=j.session_id AND l.session_created_at=j.session_created_at AND l.layer=j.layer "
            "WHERE j.chat_id=? AND j.session_id=? AND j.layer=? "
            "AND j.session_created_at=? AND j.lease_token=? AND j.claimed_version=? "
            "AND l.rewrite_identity=? AND l.purge_epoch=?",
            (
                claim.chat_id,
                claim.session_id,
                claim.layer,
                claim.session_created_at,
                claim.token,
                claim.version,
                claim.rewrite_identity,
                claim.purge_epoch,
            ),
        ).fetchone()
    )


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
                        db, claim.chat_id, claim.session_id, app_settings=app_settings
                    ):
                        fail_job(db, claim, "work_failed")
                        return "work_failed"
            for _ in range(MAX_SEGMENTS_PER_RUN):
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
                        accepted = memory_backend._retain_with_client(
                            claim.chat_id,
                            claim.session_id,
                            source.document_id,
                            fields.get("name", "Story"),
                            source.content,
                            f"Archival transcript part: {source.role}; source={source.start_id}; "
                            f"offsets={source.start_offset}:{source.end_offset}",
                            "source_segment",
                            "Hindsight segment retain unavailable for chat %s",
                            app_settings=app_settings,
                        )
                        if not accepted:
                            result = "retain_failed"
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
                            source_valid=lambda captured=source: source_is_valid(db, captured),
                        )
                        if extraction.status != "complete":
                            result = "stale_source"
                            break
                    if not store_segment(db, source):
                        result = "stale_source"
                        break
            else:
                result = (
                    "complete"
                    if next_source_segment(db, claim.chat_id, claim.session_id, claim.layer, through_id=claim.target_id)
                    is None
                    else "deferred"
                )
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
    # Reconstruct all derived layer inputs after claim, never enqueue snapshots.
    before = load_narrative_clock(db, claim.chat_id, claim.session_id)
    if claim.layer == "summary":
        from bridge.memory import generate_session_summary_result

        result = generate_session_summary_result(
            db,
            claim.chat_id,
            session,
            force=True,
            durable=True,
            max_segments=1,
            provider_port=provider_port,
            app_settings=app_settings,
        )
        if not result.complete and result.covered_until_rowid:
            return "deferred"
        covered = result.covered_until_rowid if result.complete else 0
    elif claim.layer == "scene":
        from bridge.scene_state import get_scene_state, refresh_scene_state_now

        refresh_scene_state_now(
            db,
            "",
            claim.chat_id,
            session,
            fields.get("name", "Story"),
            through_rowid=claim.target_id,
            provider_port=provider_port,
            app_settings=app_settings,
        )
        covered = get_scene_state(db, claim.chat_id, claim.session_id)[1]
    elif claim.layer == "curator":
        from bridge.memory_curator import curate_memory_now, get_curated_memory_state

        curate_memory_now(
            db,
            "",
            claim.chat_id,
            session,
            fields.get("name", "Story"),
            through_rowid=claim.target_id,
            provider_port=provider_port,
            app_settings=app_settings,
        )
        covered = get_curated_memory_state(db, claim.chat_id, claim.session_id)[1]
    elif claim.layer == "npc":
        from bridge.npc_extraction import refresh_npc_state_now
        from bridge.npc_repository import get_npc_extraction_coverage
        from bridge.npc_service import NpcService

        with write_transaction(db):
            if not _claim_current(db, claim):
                return "stale_source"
            boundary = pending_memory_invalidation(db, claim.chat_id, claim.session_id, "npc")
            if boundary is not None:
                NpcService().rollback_from_row(db, claim.chat_id, claim.session_id, boundary)
        refresh_npc_state_now(
            db,
            claim.chat_id,
            session,
            fields,
            through_rowid=claim.target_id,
            provider_port=provider_port,
            app_settings=app_settings,
        )
        covered = get_npc_extraction_coverage(db, claim.chat_id, claim.session_id)
    else:
        raise ValueError("Unknown durable memory layer")
    after = load_narrative_clock(db, claim.chat_id, claim.session_id)
    if (
        before is None
        or after is None
        or any(before[k] != after[k] for k in ("session_created_at", "rewrite_revision"))
    ):
        return "stale_source"
    if covered < claim.target_id:
        return "work_failed"
    with write_transaction(db):
        db.execute(
            "UPDATE memory_layer_state SET covered_id=? WHERE chat_id=? AND session_id=? "
            "AND session_created_at=? AND layer=?",
            (covered, claim.chat_id, claim.session_id, claim.session_created_at, claim.layer),
        )
    return "complete"


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


def dispatch_memory_backlog(services, db, *, startup=False):
    if startup:
        # One runtime owns the process; startup recovers interrupted claims.
        with write_transaction(db):
            db.execute("UPDATE memory_jobs SET lease_token='',lease_deadline=0 WHERE lease_token<>''")
    else:
        recover_expired_jobs(db)
    active = db.execute("SELECT count(*) FROM memory_jobs WHERE lease_token<>''").fetchone()[0]
    if active >= MAX_ACTIVE_CLAIMS:
        return 0
    enabled = db.execute(
        "SELECT DISTINCT layer FROM memory_jobs WHERE layer NOT IN ('hindsight','curator') OR "
        "COALESCE((SELECT value FROM meta WHERE key='memory_mode:' || memory_jobs.chat_id),'on')='on'"
    ).fetchall()
    claims = claim_jobs(db, layers=tuple(row[0] for row in enabled), limit=MAX_ACTIVE_CLAIMS - active)
    dispatched = 0
    for claim in claims:
        if claim.layer in {"hindsight", "curator"} and memory_backend.memory_mode(db, claim.chat_id) != "on":
            fail_job(db, claim, "disabled")
            continue
        if services.background.submit("hindsight_retain", _memory_worker, services, claim):
            dispatched += 1
        else:
            fail_job(db, claim, "executor_rejected")
    return dispatched
