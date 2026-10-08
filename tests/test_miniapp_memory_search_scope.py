"""Submitted recall must never follow another story selected before its worker runs."""

from contextlib import closing
from dataclasses import replace

import pytest
from miniapp_test_support import identity, make_services

from bridge.miniapp_context import current_session
from bridge.miniapp_errors import MiniAppError
from bridge.miniapp_memory import memory_status, recall_remote


def test_queued_recall_rejects_a_changed_story_before_calling_memory(tmp_path, monkeypatch):
    import bridge.miniapp_jobs as jobs

    services, who = make_services(tmp_path), identity()
    original = current_session(services, who, {})["session"]
    queued, searched = [], []
    services.memory = replace(
        services.memory, search_backend=lambda *args: searched.append(args[2]["session_id"]) or []
    )
    monkeypatch.setattr(jobs, "submit_background", lambda _label, work: queued.append(work) or True)
    job = jobs.submit_job(
        services,
        who,
        "memory_search",
        {"operation_id": "old-story-recall", "session_id": original["session_id"], "query": "the garden"},
        recall_remote,
    )
    with closing(services.db_factory()) as db:
        services.session.create(db, who.chat_id, services.config.default_model, session_id="next-story")
    queued.pop()()
    result = jobs.job_status(services, who, {"job_id": job["id"]})
    assert result["state"] == "failed"
    assert "session changed" in result["error"].lower()
    assert searched == []


@pytest.mark.parametrize("values", [{}, {"session_id": ""}, {"session_id": None}])
def test_submitted_recall_requires_the_rendered_story(tmp_path, values):
    services, who = make_services(tmp_path), identity()
    with pytest.raises(MiniAppError) as failure:
        recall_remote(services, who, {**values, "query": "the garden"})
    assert failure.value.status == 400


def test_recall_keeps_its_captured_story_without_taking_the_chat_write_lock(tmp_path):
    from bridge.background import chat_job_lock

    services, who = make_services(tmp_path), identity()
    session = current_session(services, who, {})["session"]
    with closing(services.db_factory()) as db:
        services.session.create(db, who.chat_id, services.config.default_model, session_id="other-story")
        from bridge.metadata import set_meta

        set_meta(db, f"active_session:{who.chat_id}", session["session_id"])

    def recall(db, chat_id, captured, query, character_name):
        from bridge.metadata import set_meta

        lock = chat_job_lock(chat_id)
        assert lock.acquire(blocking=False), "Read-only recall must leave story actions available"
        try:
            set_meta(db, f"active_session:{chat_id}", "other-story")
        finally:
            lock.release()
        return [{"text": captured["session_id"] + " continuity"}]

    services.memory = replace(services.memory, search_backend=recall)
    assert recall_remote(services, who, {"session_id": session["session_id"], "query": "garden"}) == {
        "results": ["default continuity"]
    }
    assert current_session(services, who, {})["session"]["session_id"] == "other-story"
    # A current-state GET may omit the form scope, but cannot ignore an explicit stale scope.
    assert memory_status(services, who, {})["session"]["session_id"] == "other-story"
    with pytest.raises(MiniAppError) as failure:
        memory_status(services, who, {"session_id": session["session_id"]})
    assert failure.value.status == 409
