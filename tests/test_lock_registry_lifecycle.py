"""Idle lock registries must not own historical chat/session lifetimes."""

import gc
import threading
import weakref

import pytest

from bridge.background import chat_job_lock
from bridge.memory_backend import hindsight_session_lock


@pytest.fixture(params=["chat", "hindsight"])
def lock_for(request):
    if request.param == "chat":
        return lambda key: chat_job_lock("registry-test:" + key)
    return lambda key: hindsight_session_lock("registry-test", key)


def test_idle_locks_are_collectible_after_scope_churn(lock_for):
    references = []
    for index in range(1000):
        lock = lock_for(str(index))
        references.append(weakref.ref(lock))
        with lock:
            pass
    del lock
    gc.collect()
    assert sum(reference() is not None for reference in references) == 0


def test_live_lock_identity_is_preserved_for_waiting_threads(lock_for):
    lock = lock_for("contended")
    reference = weakref.ref(lock)
    attempted = threading.Event()
    entered = threading.Event()
    identities = []

    def worker():
        candidate = lock_for("contended")
        identities.append(candidate is reference())
        attempted.set()
        with candidate:
            entered.set()

    thread = threading.Thread(target=worker)
    try:
        with lock:
            assert lock_for("contended") is lock
            thread.start()
            assert attempted.wait(5)
            assert identities == [True]
            assert not entered.is_set()
    finally:
        if thread.ident is not None:
            thread.join(5)
    assert not thread.is_alive()
    assert entered.is_set()


def test_hindsight_lock_remains_reentrant():
    lock = hindsight_session_lock("registry-test", "reentrant")
    with lock:
        same = hindsight_session_lock("registry-test", "reentrant")
        assert same is lock
        assert same.acquire(blocking=False)
        same.release()
