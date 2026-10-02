"""Canonical document jobs owner."""

from __future__ import annotations

import logging
from functools import partial

from bridge.background import chat_job_lock
from bridge.composition import BridgeServices as _BridgeServices
from bridge.delivery_progress import DeliveryFailure, delivery_complete
from bridge.delivery_recovery import handle_delivery_failure, resume_committed_turn
from bridge.image_messages import process_image_message
from bridge.media_policy import conversational_document
from bridge.native_imports import import_telegram_document
from bridge.request_types import RequestContext
from bridge.response_delivery import send_reply
from bridge.transcript_repository import committed_assistant_for_message
from bridge.turn_delivery_repository import turn_delivery_target


def process_document_job(
    services: _BridgeServices,
    chat_id: str,
    document: dict,
    message_id: int | None = None,
    queued_session_id: str | None = None,
    model_override: str | None = None,
    character_upload: bool = False,
    job_id: int | None = None,
) -> None:
    token = services.config.bot_token
    model = model_override or services.config.default_model
    jobs = services.jobs
    with chat_job_lock(chat_id):
        db = services.db_factory()
        claimed = False
        try:
            if job_id is not None and not jobs.start(db, job_id):
                return
            claimed = True
            if resume_committed_turn(services, db, job_id):
                return
            if job_id is not None and not queued_session_id:
                raise ValueError("durable document job requires its queued session")
            session = (
                services.session.load(db, chat_id, queued_session_id, model)
                if queued_session_id
                else services.session.ensure(db, chat_id, model)
            )
            if conversational_document(document):
                existing = committed_assistant_for_message(db, chat_id, message_id or 0)
                if existing:
                    if not delivery_complete(db, int(existing[0])):
                        send_reply(
                            token,
                            chat_id,
                            str(existing[1]),
                            db,
                            session["session_id"],
                            int(existing[0]),
                            app_settings=services.config,
                        )
                    if job_id is not None:
                        jobs.complete(db, job_id)
                    return
                if not services.group.user_turn_allowed(db, chat_id, session["session_id"], jobs.actor_id(db, job_id)):
                    if job_id is not None:
                        jobs.complete(db, job_id)
                    services.telegram.send_text(token, chat_id, "It is not your turn in manual group mode.")
                    return
            request_context = RequestContext(
                db,
                session["session_id"],
                jobs.actor_id(db, job_id),
                app_settings=services.config,
            )
            import_telegram_document(
                db,
                token,
                chat_id,
                document,
                model,
                telegram_message_id=message_id,
                api_key=services.config.api_key,
                process_image=partial(
                    process_image_message,
                    operation_id=job_id,
                    provider_port=services.provider,
                    group_service=services.group,
                    npc_service=services.npc,
                    app_settings=services.config,
                    rag_service=services.rag,
                ),
                memory_service=services.memory,
                persona_service=services.persona,
                group_director_service=services.group_director,
                app_settings=services.config,
                rag_service=services.rag,
                provider_port=services.provider,
                request_context=request_context,
                character_upload=character_upload,
            )
            if job_id is not None:
                jobs.complete(db, job_id)
        except Exception as exc:
            if not claimed:
                raise
            if isinstance(exc, DeliveryFailure) or turn_delivery_target(db, job_id) is not None:
                handle_delivery_failure(services, db, chat_id, job_id, exc)
                return
            logging.error("Document job failed: %s", exc, exc_info=True)
            if job_id is not None:
                jobs.fail(db, job_id, exc)
            services.telegram.send_text(
                token, chat_id, "Document import failed. Check the file format and size limits."
            )
        finally:
            db.close()
