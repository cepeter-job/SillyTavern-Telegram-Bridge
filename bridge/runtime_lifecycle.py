"""Process lifecycle and Telegram polling for the composed bridge runtime."""

from __future__ import annotations

import logging
import signal
import sqlite3
import threading
import time
import urllib.error

from bridge.background import shutdown_background_executors
from bridge.composition import BridgeServices
from bridge.metadata import get_meta
from bridge.runtime_health import capture_deployment
from bridge.sqlite_store import run_database_maintenance, write_transaction
from bridge.sync_api import start_live_sync_worker, stop_live_sync_worker
from bridge.update_ack import UpdateAckStatus, attempt_pending_update_ack, pending_update_ack_path
from bridge.update_routing import route_update
from bridge.worker_orchestration import make_durable_backlog_dispatcher, resolve_recovered_job_submission

_SHUTDOWN_EVENT = threading.Event()
_ACK_RETRY_INITIAL_SECONDS = 60.0
_ACK_RETRY_MAX_SECONDS = 300.0


def request_bridge_shutdown(
    on_shutdown,
    signum=None,
    _frame=None,
) -> None:
    if signum is not None:
        logging.info("Bridge shutdown requested by signal %s", signum)
    _SHUTDOWN_EVENT.set()
    on_shutdown()


def install_bridge_signal_handlers(on_shutdown) -> None:
    def handle(signum, frame):
        request_bridge_shutdown(on_shutdown, signum, frame)

    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(signum, handle)
        except (ValueError, OSError):
            logging.debug(
                "Could not install signal handler %s",
                signum,
                exc_info=True,
            )


def restore_poll_offset(db: sqlite3.Connection, fallback: int) -> int:
    try:
        return int(get_meta(db, "telegram_offset", str(fallback)) or fallback)
    except (TypeError, ValueError, sqlite3.Error):
        return int(fallback)


def run_bridge_runtime(services: BridgeServices, fields: dict) -> int:
    config = services.config
    memory_diagnostics = getattr(services, "memory_diagnostics", None)
    if memory_diagnostics is not None:
        try:
            memory_diagnostics.start()
        except Exception:
            logging.warning("Memory diagnostics startup failed")
    token = config.bot_token
    health = getattr(services, "health", None)
    if health is not None:
        health.begin(config)
    deployment = health.deployment if health is not None else capture_deployment(config)
    pending_at_boot = pending_update_ack_path(config).exists()
    ack_terminal = False
    ack_retry_at = 0.0
    ack_retry_delay = _ACK_RETRY_INITIAL_SECONDS
    first_poll = True
    _SHUTDOWN_EVENT.clear()
    install_bridge_signal_handlers(services.background.begin_shutdown)
    db = services.db_factory()
    start_live_sync_worker(sync_service=services.sync, app_settings=services.config)
    services.background.register_backlog_dispatcher(make_durable_backlog_dispatcher(services, fields))
    # Only startup may reset leases. Ordinary backlog dispatch must not race an active choice call.
    with write_transaction(db):
        db.execute("UPDATE light_novel_choice_sets SET lease_token='',lease_until=0 WHERE generation_status='pending'")
    services.jobs.recover(
        db,
        lambda job: resolve_recovered_job_submission(
            services,
            fields,
            job,
        ),
        recover_running=True,
    )
    offset = int(get_meta(db, "telegram_offset", "0"))
    permitted = config.allowed_users
    logging.info("Bridge started")
    last_safe_offset = offset
    while not _SHUTDOWN_EVENT.is_set():
        try:
            updates = services.telegram.request(
                token,
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": 0 if first_poll else 50,
                    "allowed_updates": [
                        "message",
                        "edited_message",
                        "callback_query",
                    ],
                },
            )
            first_poll = False
            if health is not None:
                health.poll_succeeded()
            now = time.monotonic()
            if pending_at_boot and not ack_terminal and now >= ack_retry_at:
                ack_status = attempt_pending_update_ack(
                    token,
                    app_settings=config,
                    send_text_backend=services.telegram.send_text,
                    version_backend=lambda **kwargs: deployment.version,
                    commit_backend=lambda **kwargs: deployment.commit,
                )
                if ack_status is UpdateAckStatus.RETRYABLE_ERROR:
                    ack_retry_at = now + ack_retry_delay
                    logging.info("Update acknowledgement retry deferred for %.0fs", ack_retry_delay)
                    ack_retry_delay = min(_ACK_RETRY_MAX_SECONDS, ack_retry_delay * 2)
                else:
                    ack_terminal = True
            for update in updates:
                last_safe_offset = offset
                offset = route_update(
                    services,
                    db,
                    fields,
                    update,
                    offset,
                    permitted,
                )
        except urllib.error.HTTPError as exc:
            if health is not None:
                health.poll_failed()
            logging.error("Telegram HTTP error: %s", exc.code)
            _SHUTDOWN_EVENT.wait(10)
        except KeyboardInterrupt:
            request_bridge_shutdown(services.background.begin_shutdown)
            break
        except Exception as exc:
            if health is not None:
                health.poll_failed()
            if _SHUTDOWN_EVENT.is_set():
                break
            offset = restore_poll_offset(db, last_safe_offset)
            logging.error("Polling error: %s", exc, exc_info=True)
            _SHUTDOWN_EVENT.wait(5)

    request_bridge_shutdown(services.background.begin_shutdown)
    if health is not None:
        health.stopping()
    if memory_diagnostics is not None:
        try:
            memory_diagnostics.stop(timeout=1.0)
        except Exception:
            logging.warning("Memory diagnostics shutdown failed")
    sync_stopped = stop_live_sync_worker(timeout=5.0)
    drained = shutdown_background_executors(timeout=20.0)
    db.close()
    # Explicit maintenance on a dedicated connection after executors drained:
    # VACUUM never competes with durable job transitions on the live handle.
    run_database_maintenance(app_settings=services.config)
    if not sync_stopped:
        logging.warning("Realtime sync worker did not stop before shutdown deadline")
    if not drained:
        logging.warning("Background jobs exceeded the graceful shutdown deadline")
    logging.info("Bridge stopped")
    return 0
