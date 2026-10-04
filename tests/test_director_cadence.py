"""Planning is event-driven and bounded, never an unconditional per-turn tax."""

from dataclasses import replace
from unittest.mock import patch

import pytest
from application_test_setup import make_test_persona_service, make_test_provider_port
from test_director_service import directed as directed
from test_director_service import reassess
from test_memory_completion_safety import session_db as session_db
from test_narrative_reconciliation import add_story, proposal, run_reconciliation

from bridge import director_runtime, extension_registry, narrative_reconciliation
from bridge.director_cadence import director_due, director_event_key, director_interval
from bridge.director_repository import load_director_state
from bridge.extension_context import PostRetainContext
from bridge.narrative_context import narrative_context_for_session
from bridge.narrative_repository import load_narrative_clock
from bridge.narrative_settings import preset_narrative_settings
from bridge.narrative_values import NarrativeState
from bridge.sqlite_store import write_transaction


@pytest.mark.parametrize("phase,cap", [("setup", 10), ("development", 6), ("escalation", 4), ("climax", 2)])
def test_adaptive_caps_and_no_calls_below_threshold(phase, cap):
    settings = preset_narrative_settings("player_centric")
    current = {"last_director_turn": 0, "last_event_key": "same"}
    state = NarrativeState(story_phase=phase, active_scene_id="gate")
    assert director_interval(settings, phase) == cap
    assert not director_due(current, state, settings=settings, completed_turns=cap - 1, now=1000, event_key="same")
    assert director_due(current, state, settings=settings, completed_turns=cap, now=1000, event_key="same")


def test_fixed_cadence_overrides_phase_but_meaningful_events_run_earlier():
    settings = replace(
        preset_narrative_settings("world_driven"), director_cadence_mode="fixed", director_fixed_interval=17
    )
    state = NarrativeState(story_phase="climax", active_scene_id="gate")
    assert director_interval(settings, "climax") == 17
    assert not director_due({}, state, settings=settings, completed_turns=16, now=1000)
    assert director_due(
        {"last_event_key": "old"}, state, settings=settings, completed_turns=2, now=1000, event_key="new"
    )


@pytest.mark.parametrize("phase", ["resolution", "epilogue", "closed"])
def test_normal_cadence_stops_during_ending_pipeline(phase):
    assert not director_due(
        {},
        NarrativeState(story_phase=phase),
        settings=preset_narrative_settings("observer"),
        completed_turns=200,
        now=1000,
        event="manual",
    )


def test_expired_direction_fires_before_maximum_and_manual_scope_wins():
    settings = preset_narrative_settings("world_driven")
    state = NarrativeState(active_scene_id="gate", story_phase="development")
    current = {
        "active_direction": "Old plan",
        "direction_source": "ai",
        "direction_until_turn": 3,
        "last_director_turn": 1,
    }
    assert director_due(current, state, settings=settings, completed_turns=3, now=1000)
    current |= {"direction_source": "user", "accepted_scene_id": "gate"}
    assert not director_due(current, state, settings=settings, completed_turns=100, now=1000)
    assert director_due(
        current, replace(state, active_scene_id="palace"), settings=settings, completed_turns=100, now=1000
    )


def test_busy_lease_and_failure_backoff_prevent_duplicate_or_hot_retry_calls():
    settings = preset_narrative_settings("world_driven")
    state = NarrativeState(story_phase="development", active_scene_id="gate")
    assert not director_due(
        {"inflight_token": "busy", "inflight_started_at": 990},
        state,
        settings=settings,
        completed_turns=10,
        now=1000,
        event="manual",
    )
    assert not director_due(
        {"last_attempt_at": 995, "degraded_state": "timeout"}, state, settings=settings, completed_turns=10, now=1000
    )
    assert director_due(
        {"last_attempt_at": 995, "degraded_state": "timeout"},
        state,
        settings=settings,
        completed_turns=10,
        now=1000,
        event="manual",
    )


def test_event_fingerprint_ignores_prose_churn_but_tracks_actual_narrative_changes():
    state = NarrativeState(
        active_scene_id="gate", active_thread_id="rebellion", story_phase="development", user_present=False
    )
    clock = {"settings_revision": 1, "rewrite_revision": 0}
    threads = [{"thread_id": "rebellion", "status": "active", "summary": "one"}]
    old = director_event_key(state, clock, threads)
    assert len(old) == 64
    assert director_event_key(state, clock, [threads[0] | {"summary": "reworded"}]) == old
    assert director_event_key(replace(state, user_present=True), clock, threads) != old
    assert director_event_key(state, clock | {"rewrite_revision": 1}, threads) != old
    assert director_event_key(state, clock, [threads[0] | {"status": "resolved"}]) != old


def test_typed_retention_context_preserves_the_real_persona_service(session_db):
    settings, db, session = session_db
    persona = make_test_persona_service()
    provider = make_test_provider_port()
    seen = []
    with patch.dict(extension_registry._POST_RETAIN_HOOKS, clear=True):
        extension_registry.register_post_retain_hook("capture", seen.append)
        extension_registry.run_post_retain_hooks(
            db, "chat", session, {"name": "Mara"}, provider, app_settings=settings, persona_service=persona
        )
    assert isinstance(seen[0], PostRetainContext)
    assert seen[0].persona_service is persona
    assert seen[0].provider_port is provider
    assert seen[0].app_settings is settings


def test_queue_is_single_flight_and_releases_scope_after_worker(directed, monkeypatch):
    settings, db, session = directed
    for _ in range(5):
        add_story(db)
    run_reconciliation(directed)
    queued = []
    monkeypatch.setattr(
        director_runtime, "submit_background", lambda label, fn, *a, **k: queued.append((fn, a, k)) or True
    )
    seen = []
    monkeypatch.setattr(director_runtime.DirectorService, "reassess", lambda *a, **k: seen.append(k))
    persona = make_test_persona_service()
    args = dict(provider_port=make_test_provider_port(), app_settings=settings, persona_service=persona)
    assert director_runtime.queue_director_reassessment(db, "chat", session, **args)
    assert not director_runtime.queue_director_reassessment(db, "chat", session, **args)
    fn, a, k = queued.pop(0)
    fn(*a, **k)
    assert seen[0]["persona_service"] is persona
    assert director_runtime.queue_director_reassessment(db, "chat", session, **args)
    fn, a, k = queued.pop(0)
    fn(*a, **k)


def test_rejected_background_admission_does_not_leak_session_lock(directed, monkeypatch):
    settings, db, session = directed
    for _ in range(5):
        add_story(db)
    run_reconciliation(directed)
    monkeypatch.setattr(director_runtime, "submit_background", lambda *a, **k: False)
    args = dict(provider_port=make_test_provider_port(), app_settings=settings)
    assert not director_runtime.queue_director_reassessment(db, "chat", session, **args)
    queued = []
    monkeypatch.setattr(
        director_runtime, "submit_background", lambda label, fn, *a, **k: queued.append((fn, a, k)) or True
    )
    monkeypatch.setattr(director_runtime.DirectorService, "reassess", lambda *a, **k: None)
    assert director_runtime.queue_director_reassessment(db, "chat", session, **args)
    fn, a, k = queued.pop()
    fn(*a, **k)


def test_first_scene_is_free_but_a_new_committed_scene_is_an_event(directed, monkeypatch):
    settings, db, session = directed
    queued = []
    monkeypatch.setattr(
        director_runtime, "submit_background", lambda label, fn, *a, **k: queued.append((fn, a, k)) or True
    )
    monkeypatch.setattr(director_runtime.DirectorService, "reassess", lambda *a, **k: None)
    args = dict(provider_port=make_test_provider_port(), app_settings=settings)
    assert not director_runtime.queue_director_reassessment(db, "chat", session, **args)
    assert load_director_state(db, "chat", "s1")["last_event_key"]
    add_story(db, "Mara reaches the palace.")
    run_reconciliation(directed, lambda *a, **k: proposal("palace", transition="cut"))
    assert director_runtime.queue_director_reassessment(db, "chat", session, **args)
    fn, a, k = queued.pop()
    fn(*a, **k)


def test_reconciliation_completion_triggers_planning_only_after_facts_are_current(directed, monkeypatch):
    settings, db, session = directed
    add_story(db)
    queued = []
    monkeypatch.setattr(
        narrative_reconciliation, "submit_background", lambda label, fn, *a, **k: queued.append((fn, a, k)) or True
    )
    observed = []
    monkeypatch.setattr(
        director_runtime,
        "queue_director_reassessment",
        lambda database, chat, story, **kw: (
            observed.append((load_narrative_clock(database, chat, story["session_id"]), kw)) or False
        ),
    )
    persona = make_test_persona_service()
    context = PostRetainContext(
        db,
        "chat",
        session,
        {"name": "Mara"},
        make_test_provider_port(generate_backend=lambda *a, **k: proposal()),
        settings,
        persona,
    )
    director_runtime._post_retain(context)
    assert not observed
    fn, a, k = queued.pop()
    fn(*a, **k)
    assert observed[0][0]["updated_through_rowid"] == observed[0][0]["latest_rowid"]
    assert observed[0][1]["persona_service"] is persona


def test_actual_story_context_receives_only_valid_accepted_plan(directed):
    _settings, db, _session = directed
    reassess(directed)
    assert "Keep pressure on the gate" in narrative_context_for_session(db, "chat", "s1", "story")
    with write_transaction(db):
        db.execute("UPDATE messages SET content='The old scene is wrong.'")
    assert "Keep pressure on the gate" not in narrative_context_for_session(db, "chat", "s1", "story")


def test_queue_after_closure_never_calls_provider_or_writes_director(directed, monkeypatch):
    settings, db, session = directed
    with write_transaction(db):
        db.execute("INSERT INTO ending_state(chat_id,session_id,lifecycle) VALUES('chat','s1','closed')")
    before = db.total_changes
    monkeypatch.setattr(
        director_runtime, "submit_background", lambda *a, **k: pytest.fail("closed story queued planning")
    )
    assert not director_runtime.queue_director_reassessment(
        db, "chat", session, provider_port=make_test_provider_port(), app_settings=settings
    )
    assert db.total_changes == before


def test_production_memory_composition_carries_its_own_persona_port(session_db):
    from application_test_setup import make_test_model_router

    from bridge.main import _build_startup_services
    from bridge.metadata import set_meta

    settings, db, session = session_db
    services = _build_startup_services(settings, model_router=make_test_model_router())
    set_meta(db, "memory_mode:chat", "off")
    seen = []
    with patch.dict(extension_registry._POST_RETAIN_HOOKS, clear=True):
        extension_registry.register_post_retain_hook("capture", seen.append)
        services.memory.retain(db, "chat", session, {"name": "Mara"})
    assert seen[0].persona_service is services.persona
    assert seen[0].provider_port is services.provider
