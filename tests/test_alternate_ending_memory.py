"""Alternate-ending seeding uses only target-scoped identities in the shared chat bank."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from test_alternate_ending import branch, close_story
from test_memory_completion_safety import session_db as session_db

from bridge.meta_repository import store_meta_value
from bridge.sqlite_store import write_transaction


def test_disabled_external_memory_makes_no_network_call(session_db, monkeypatch):
    from bridge import memory_backend
    from bridge.alternate_ending_memory import seed_alternate_ending_memory

    config, db, _ = session_db
    cp = close_story(session_db)
    result = branch(session_db, cp)
    with write_transaction(db):
        store_meta_value(db, "memory_mode:chat", "off")
    monkeypatch.setattr(memory_backend, "hindsight_client_scope", lambda **k: pytest.fail("Disabled network call"))
    assert seed_alternate_ending_memory(db, "chat", result.session, app_settings=config) == "disabled"


def test_target_tags_and_document_ids_are_distinct_and_deterministic(session_db, monkeypatch):
    from bridge import memory_backend
    from bridge.alternate_ending_memory import seed_alternate_ending_memory

    config, db, _ = session_db
    cp = close_story(session_db)
    target = branch(session_db, cp).session
    with write_transaction(db):
        store_meta_value(db, "memory_mode:chat", "on")
    observed = []

    @contextmanager
    def client_scope(**kwargs):
        yield SimpleNamespace(retain=lambda **kw: observed.append(kw))

    monkeypatch.setattr(memory_backend, "hindsight_client_scope", client_scope)
    assert seed_alternate_ending_memory(db, "chat", target, app_settings=config) == "ready"
    assert seed_alternate_ending_memory(db, "chat", target, app_settings=config) == "ready"
    assert observed[0]["bank_id"] == memory_backend.hindsight_bank_id("chat")
    assert len(observed) == 1  # second call reuses completed source coverage
    assert observed[0]["document_id"].startswith("session:" + target["session_id"] + ":source:")
    assert "session:" + target["session_id"] in observed[0]["tags"]
    assert "session:s1" not in observed[0]["tags"]
    assert "The governor surrendered" not in observed[0]["content"]
    assert memory_backend.memory_recall_filter(db, "chat", target, "") == [
        "session:" + target["session_id"],
        "native-fact",
    ]
    assert not db.in_transaction


def test_seed_reports_degraded_without_falling_back_to_source_memory(session_db, monkeypatch):
    from bridge import memory_backend
    from bridge.alternate_ending_memory import seed_alternate_ending_memory

    config, db, _ = session_db
    cp = close_story(session_db)
    target = branch(session_db, cp).session
    with write_transaction(db):
        store_meta_value(db, "memory_mode:chat", "on")
    calls = []
    monkeypatch.setattr(memory_backend, "_retain_with_client", lambda *a, **k: calls.append(a) or False)
    assert seed_alternate_ending_memory(db, "chat", target, app_settings=config) == "degraded"
    assert len(calls) == 1 and calls[0][1] == target["session_id"]
