"""Bounded process-wide job scheduling, admission, and executor lifecycle."""

from __future__ import annotations

import concurrent.futures
import logging
import threading
import time
import weakref
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import ParamSpec, TypeVar

import bridge.limits as _limits

P = ParamSpec("P")
T = TypeVar("T")

_GENERATION_SLOTS = threading.BoundedSemaphore(6)


_UTILITY_SLOTS = threading.BoundedSemaphore(4)


_MEMORY_SLOTS = threading.BoundedSemaphore(4)


_GENERATION_EXECUTOR: concurrent.futures.ThreadPoolExecutor | None = None


_UTILITY_EXECUTOR: concurrent.futures.ThreadPoolExecutor | None = None


_MEMORY_EXECUTOR: concurrent.futures.ThreadPoolExecutor | None = None


_GENERATION_LABELS = {"generation", "command", "retry", "regen", "continue", "edit", "summarize"}
_MEMORY_LABELS = {"hindsight_retain", "memory_curator", "scene_state_refresh", "npc_state_refresh"}


_CHAT_LOCKS: weakref.WeakValueDictionary[str, threading.Lock] = weakref.WeakValueDictionary()


_CHAT_LOCKS_GUARD = threading.Lock()


_BACKGROUND_STATE_LOCK = threading.Lock()


_EXECUTOR_LOCK = threading.Lock()


_BACKGROUND_FUTURES: set[concurrent.futures.Future] = set()
_BACKGROUND_FUTURE_LABELS: dict[concurrent.futures.Future, str] = {}


_BACKGROUND_ACCEPTING = True


def _runtime_process_snapshot() -> tuple[int, int]:
    rss_kib = 0
    thread_count = threading.active_count()
    try:
        for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition(":")
            if not separator:
                continue
            if key == "VmRSS":
                parts = value.split()
                if len(parts) == 2 and parts[0].isdigit() and parts[1] == "kB":
                    rss_kib = int(parts[0])
            elif key == "Threads":
                raw = value.strip()
                if raw.isdigit():
                    thread_count = int(raw)
    except OSError:
        pass
    return rss_kib, thread_count


def _run_observed_background(label: str, function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    before_rss, before_threads = _runtime_process_snapshot()
    started = time.monotonic()
    logging.info("background_start label=%s rss_kib=%s threads=%s", label, before_rss, before_threads)
    status = "failed"
    try:
        result = function(*args, **kwargs)
        status = "succeeded"
        return result
    finally:
        after_rss, after_threads = _runtime_process_snapshot()
        duration_ms = max(0, int((time.monotonic() - started) * 1000))
        logging.info(
            "background_finish label=%s status=%s duration_ms=%s rss_kib=%s rss_delta_kib=%s threads=%s",
            label,
            status,
            duration_ms,
            after_rss,
            after_rss - before_rss,
            after_threads,
        )


def chat_job_lock(chat_id: str) -> threading.Lock:
    """Share a lock while a holder or waiter owns it; do not retain idle scopes."""
    with _CHAT_LOCKS_GUARD:
        return _CHAT_LOCKS.setdefault(str(chat_id), threading.Lock())


def _executor_for(label: str) -> concurrent.futures.ThreadPoolExecutor:
    global _GENERATION_EXECUTOR, _UTILITY_EXECUTOR, _MEMORY_EXECUTOR

    with _EXECUTOR_LOCK:
        if label in _GENERATION_LABELS:
            if _GENERATION_EXECUTOR is None:
                _GENERATION_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
                    max_workers=3,
                    thread_name_prefix="st-generation",
                )
            return _GENERATION_EXECUTOR

        if label in _MEMORY_LABELS:
            if _MEMORY_EXECUTOR is None:
                _MEMORY_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
                    max_workers=1,
                    thread_name_prefix="st-memory",
                )
            return _MEMORY_EXECUTOR

        if _UTILITY_EXECUTOR is None:
            _UTILITY_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
                max_workers=2,
                thread_name_prefix="st-utility",
            )
        return _UTILITY_EXECUTOR


def _admission_slot(label: str) -> threading.BoundedSemaphore:
    if label in _GENERATION_LABELS:
        return _GENERATION_SLOTS
    if label in _MEMORY_LABELS:
        return _MEMORY_SLOTS
    return _UTILITY_SLOTS


def background_jobs_accepting() -> bool:
    with _BACKGROUND_STATE_LOCK:
        return bool(_BACKGROUND_ACCEPTING)


def _submit_tracked_future(
    label: str, function: Callable[P, T], *args: P.args, **kwargs: P.kwargs
) -> concurrent.futures.Future[T] | None:
    with _BACKGROUND_STATE_LOCK:
        if not _BACKGROUND_ACCEPTING:
            return None

        def observed() -> T:
            return _run_observed_background(label, function, *args, **kwargs)

        try:
            future = _executor_for(label).submit(observed)
        except RuntimeError:
            logging.info("Background executor is shutting down; rejected %s job", label)
            return None
        _BACKGROUND_FUTURES.add(future)
        _BACKGROUND_FUTURE_LABELS[future] = label

    def forget(done: concurrent.futures.Future[T]) -> None:
        with _BACKGROUND_STATE_LOCK:
            _BACKGROUND_FUTURES.discard(done)
            _BACKGROUND_FUTURE_LABELS.pop(done, None)

    future.add_done_callback(forget)
    return future


def drain_background_jobs(timeout: float = 20.0) -> bool:
    """Wait for already-submitted work without accepting new jobs."""
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        with _BACKGROUND_STATE_LOCK:
            futures = tuple(_BACKGROUND_FUTURES)
        if not futures or all(future.done() for future in futures):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        concurrent.futures.wait(futures, timeout=remaining)


def active_background_job_labels() -> tuple[str, ...]:
    """Return safe labels for work that is still running or queued."""
    with _BACKGROUND_STATE_LOCK:
        labels = [
            _BACKGROUND_FUTURE_LABELS.get(future, "unlabeled") for future in _BACKGROUND_FUTURES if not future.done()
        ]
    return tuple(sorted(labels))


def background_observability_counters() -> dict[str, int]:
    with _BACKGROUND_STATE_LOCK:
        active = [
            (future, _BACKGROUND_FUTURE_LABELS.get(future, "unlabeled"))
            for future in _BACKGROUND_FUTURES
            if not future.done()
        ]
    running = sum(1 for future, _label in active if future.running())
    result: dict[str, int] = {
        "background.active_total": len(active),
        "background.running_total": running,
        "background.queued_total": len(active) - running,
    }
    labels = sorted({label for _future, label in active if label.replace("_", "").isalnum()})[:12]
    for label in labels:
        label_running = sum(1 for future, current in active if current == label and future.running())
        label_total = sum(1 for _future, current in active if current == label)
        if label_running:
            result[f"background.{label}.running"] = label_running
        if label_total - label_running:
            result[f"background.{label}.queued"] = label_total - label_running
    return result


def submit_background(label: str, function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> bool:
    if not background_jobs_accepting():
        logging.info("Background shutdown in progress; rejected %s job", label)
        return False
    slot = _admission_slot(label)
    if not slot.acquire(blocking=False):
        logging.warning("Background queue full; dropping %s job", label)
        return False
    future = _submit_tracked_future(label, function, *args, **kwargs)
    if future is None:
        slot.release()
        return False

    def complete(done: concurrent.futures.Future) -> None:
        slot.release()
        error = None if done.cancelled() else done.exception()
        if error:
            logging.error(
                "Background %s job failed: %s", label, error, exc_info=(type(error), error, error.__traceback__)
            )
        _dispatch_waiting_chat_jobs()

    future.add_done_callback(complete)
    return True


_DURABLE_BACKLOG_DISPATCHER: Callable[[], None] | None = None


def register_durable_backlog_dispatcher(callback: Callable[[], None] | None) -> None:
    global _DURABLE_BACKLOG_DISPATCHER
    _DURABLE_BACKLOG_DISPATCHER = callback


def begin_background_shutdown() -> None:
    """Stop accepting work; durable chat jobs remain recoverable in SQLite."""
    global _BACKGROUND_ACCEPTING, _DURABLE_BACKLOG_DISPATCHER
    with _BACKGROUND_STATE_LOCK:
        _BACKGROUND_ACCEPTING = False
    _DURABLE_BACKLOG_DISPATCHER = None


def shutdown_background_executors(timeout: float = 20.0) -> bool:
    global _GENERATION_EXECUTOR, _UTILITY_EXECUTOR, _MEMORY_EXECUTOR

    begin_background_shutdown()
    drained = drain_background_jobs(timeout)
    if not drained:
        labels = active_background_job_labels()
        logging.warning(
            "Background shutdown deadline reached with %d unfinished job(s): %s",
            len(labels),
            ", ".join(labels) or "unknown",
        )

    with _EXECUTOR_LOCK:
        generation = _GENERATION_EXECUTOR
        utility = _UTILITY_EXECUTOR
        memory = _MEMORY_EXECUTOR
        _GENERATION_EXECUTOR = None
        _UTILITY_EXECUTOR = None
        _MEMORY_EXECUTOR = None

    if generation is not None:
        generation.shutdown(wait=drained, cancel_futures=not drained)
    if utility is not None:
        utility.shutdown(wait=drained, cancel_futures=not drained)
    if memory is not None:
        memory.shutdown(wait=drained, cancel_futures=not drained)
    return drained


_CHAT_QUEUES: dict[str, deque[tuple[str, Callable, tuple, dict]]] = {}


_CHAT_ACTIVE: set[str] = set()


_CHAT_IN_FLIGHT: set[str] = set()


def _dispatch_waiting_chat_jobs() -> None:
    if not background_jobs_accepting():
        return
    with _CHAT_LOCKS_GUARD:
        chat_ids = list(_CHAT_QUEUES)
    for chat_id in chat_ids:
        _start_next_chat_job(chat_id)
    if _DURABLE_BACKLOG_DISPATCHER is not None:
        try:
            _DURABLE_BACKLOG_DISPATCHER()
        except Exception:
            logging.warning("Durable backlog dispatcher failed", exc_info=True)


def _start_next_chat_job(chat_id: str) -> None:
    if not background_jobs_accepting():
        return
    chat_id = str(chat_id)
    with _CHAT_LOCKS_GUARD:
        if chat_id in _CHAT_IN_FLIGHT:
            return
        queue = _CHAT_QUEUES.get(chat_id, deque())
        if not queue:
            _CHAT_ACTIVE.discard(chat_id)
            _CHAT_QUEUES.pop(chat_id, None)
            return
        slot = _admission_slot(queue[0][0])
        if not slot.acquire(blocking=False):
            return
        label, function, args, kwargs = queue.popleft()
        _CHAT_IN_FLIGHT.add(chat_id)
    future = _submit_tracked_future(label, function, *args, **kwargs)
    if future is None:
        with _CHAT_LOCKS_GUARD:
            _CHAT_IN_FLIGHT.discard(chat_id)
            _CHAT_QUEUES.setdefault(chat_id, deque()).appendleft((label, function, args, kwargs))
        slot.release()
        return

    def complete(done: concurrent.futures.Future) -> None:
        slot.release()
        with _CHAT_LOCKS_GUARD:
            _CHAT_IN_FLIGHT.discard(chat_id)
        error = None if done.cancelled() else done.exception()
        if error:
            logging.error(
                "Ordered background %s job failed: %s", label, error, exc_info=(type(error), error, error.__traceback__)
            )
        _start_next_chat_job(chat_id)
        _dispatch_waiting_chat_jobs()

    future.add_done_callback(complete)


def submit_chat_background(
    label: str, chat_id: str, function: Callable[P, T], *args: P.args, **kwargs: P.kwargs
) -> bool:
    if not background_jobs_accepting():
        logging.info("Background shutdown in progress; durable %s job remains in SQLite", label)
        return False
    chat_id = str(chat_id)
    with _CHAT_LOCKS_GUARD:
        if chat_id not in _CHAT_QUEUES and len(_CHAT_QUEUES) >= _limits._BACKGROUND_MAX_SCOPED_QUEUES:
            logging.warning("Global ordered queue limit reached; durable job remains in SQLite")
            return False
        queue = _CHAT_QUEUES.setdefault(chat_id, deque())
        if len(queue) >= _limits._BACKGROUND_MAX_QUEUED_PER_CHAT:
            logging.warning("Ordered queue full for %s; durable job remains in SQLite", chat_id)
            return False
        queue.append((label, function, args, kwargs))
        should_start = chat_id not in _CHAT_ACTIVE
        if should_start:
            _CHAT_ACTIVE.add(chat_id)
    if should_start:
        _start_next_chat_job(chat_id)
    return True
