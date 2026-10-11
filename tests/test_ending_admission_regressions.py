"""Readiness remains reachable before finale entry on every Director adapter."""

from dataclasses import replace

import pytest
from application_test_setup import make_test_persona_service, make_test_provider_port
from test_director_ending import decision
from test_director_panels import payload
from test_ending_state import ready_case
from test_memory_completion_safety import session_db as session_db
from test_narrative_arcs import arc, packet
from test_narrative_reconciliation import add_story, run_reconciliation

from bridge import director_callbacks, director_runtime
from bridge.director_cadence import director_due
from bridge.director_room import director_room
from bridge.ending_service import load_ending_state
from bridge.narrative_repository import load_narrative_clock
from bridge.narrative_settings import load_session_narrative_settings, save_session_narrative_settings
from bridge.narrative_values import NarrativeSettings, NarrativeState
from bridge.request_types import RequestContext


def _ready_proposal(db):
    return decision(db, finale_ready=True, finale_reason="The established conflict is ready for its finale.")


@pytest.mark.parametrize("mode", ["closed_story", "open_ended"])
@pytest.mark.parametrize(
    "lifecycle",
    ["open", "finale_ready", "finale", "resolution_committed", "epilogue_pending", "epilogue_committed", "closed"],
)
def test_resolution_readiness_admission_is_limited_to_unentered_closed_stories(mode, lifecycle):
    assert director_due(
        {},
        NarrativeState(story_phase="resolution"),
        settings=NarrativeSettings(ending_mode=mode),
        completed_turns=100,
        now=1000,
        event="ending",
        ending_lifecycle=lifecycle,
    ) is (mode == "closed_story" and lifecycle in {"open", "finale_ready"})


@pytest.mark.parametrize("confirmation", [False, True])
def test_resolution_before_finale_is_assessed_and_respects_confirmation(session_db, monkeypatch, confirmation):
    settings, db, session = session_db
    ready_case(session_db)
    pref = load_session_narrative_settings(db, "chat", "s1")
    save_session_narrative_settings(db, "chat", "s1", replace(pref, require_finale_confirmation=confirmation))
    for _ in range(6):
        add_story(db, "The gate conflict reaches its resolution.")
    run_reconciliation(session_db, lambda *a, **k: packet(arc(), phase="resolution"))
    assert load_ending_state(db, "chat", "s1").lifecycle == "open"
    queued = []
    monkeypatch.setattr(
        director_runtime, "submit_background", lambda label, fn, *args, **kw: queued.append((fn, args, kw)) or True
    )
    assert director_runtime.queue_director_reassessment(
        db,
        "chat",
        session,
        provider_port=make_test_provider_port(generate_backend=lambda *a, **k: _ready_proposal(db)),
        app_settings=settings,
        persona_service=make_test_persona_service(),
    )
    fn, args, kwargs = queued.pop()
    fn(*args, **kwargs)
    ending = load_ending_state(db, "chat", "s1")
    assert ending.lifecycle == ("finale_ready" if confirmation else "finale")
    assert bool(ending.checkpoint_id) is not confirmation
    assert ending.finale_ready_revision == load_narrative_clock(db, "chat", "s1")["state_revision"]


@pytest.mark.parametrize("confirmation", [False, True])
def test_accepted_telegram_reassessment_enters_only_automatic_finale(session_db, monkeypatch, confirmation):
    settings, db, session = session_db
    ready_case(session_db)
    pref = load_session_narrative_settings(db, "chat", "s1")
    save_session_narrative_settings(db, "chat", "s1", replace(pref, require_finale_confirmation=confirmation))
    run_reconciliation(session_db, lambda *a, **k: packet(arc(), phase="escalation"))
    monkeypatch.setattr(director_callbacks, "send_text", lambda *a, **k: None)
    monkeypatch.setattr(director_callbacks, "send_director_menu", lambda *a, **k: None)
    data = payload(db, "reassess", director_room(db, "chat", "s1")["revision"])
    assert director_callbacks.handle_director_callback(
        db,
        "token",
        {"id": "cb"},
        lambda *a: None,
        data,
        "chat",
        {},
        session,
        "s1",
        None,
        provider_port=make_test_provider_port(generate_backend=lambda *a, **k: _ready_proposal(db)),
        persona_service=make_test_persona_service(),
        request_context=RequestContext(db, "s1", "alice", app_settings=settings),
    )
    ending = load_ending_state(db, "chat", "s1")
    assert ending.lifecycle == ("finale_ready" if confirmation else "finale")
    assert bool(ending.checkpoint_id) is not confirmation
