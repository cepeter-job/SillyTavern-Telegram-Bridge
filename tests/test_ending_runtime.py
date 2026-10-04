"""Ending execution is admitted after committed facts, with bounded restart recovery."""

from dataclasses import replace
from unittest.mock import patch

import pytest
from application_test_setup import make_test_delivery_port, make_test_persona_service, make_test_provider_port
from test_epilogue_service import deliveries, producer_for, resolved
from test_finale_resolution import finale, resolved_packet
from test_memory_completion_safety import session_db as session_db
from test_narrative_checkpoints import prepared as ready_story
from test_narrative_reconciliation import add_story

from bridge.card_content import card_fields
from bridge.ending_service import load_ending_state
from bridge.extension_context import PostRetainContext
from bridge.narrative_settings import load_session_narrative_settings, save_session_narrative_settings


def test_ready_story_enters_finale_automatically_only_without_confirmation(session_db):
    from bridge.ending_runtime import maybe_enter_finale

    _, db, _ = session_db
    ready_story(session_db)
    assert maybe_enter_finale(db, "chat", "s1")
    first = load_ending_state(db, "chat", "s1")
    assert first.lifecycle == "finale" and first.checkpoint_id
    assert not maybe_enter_finale(db, "chat", "s1")
    assert load_ending_state(db, "chat", "s1") == first


def test_confirmation_mode_never_enters_finale_from_background(session_db):
    from test_narrative_reconciliation import run_reconciliation

    from bridge.ending_runtime import maybe_enter_finale
    from bridge.ending_service import mark_finale_ready
    from bridge.narrative_repository import load_narrative_clock

    _, db, _ = session_db
    ready_story(session_db)
    pref = load_session_narrative_settings(db, "chat", "s1")
    save_session_narrative_settings(db, "chat", "s1", replace(pref, require_finale_confirmation=True))
    from test_narrative_arcs import arc, packet

    run_reconciliation(session_db, lambda *a, **k: packet(arc(), phase="escalation"))
    # Settings changes invalidate the old readiness; refresh it against the exact new clock.
    state = load_ending_state(db, "chat", "s1")
    clock = load_narrative_clock(db, "chat", "s1")
    mark_finale_ready(
        db,
        "chat",
        "s1",
        story_revision=clock["state_revision"],
        expected_lifecycle_revision=state.lifecycle_revision,
        expected_goal_revision=state.goal_revision,
        direction_revision=1,
        reason="The conflict is developed.",
    )
    assert not maybe_enter_finale(db, "chat", "s1")
    assert load_ending_state(db, "chat", "s1").lifecycle == "finale_ready"


def test_post_retain_reconciles_finale_before_scheduling_epilogue(session_db, monkeypatch):
    from bridge import director_runtime, ending_runtime

    settings, db, session = session_db
    finale(session_db)
    quote = "The governor surrendered. The rebellion ended peacefully."
    rowid = add_story(db, quote)
    scheduled = []
    monkeypatch.setattr(
        ending_runtime,
        "queue_ending_recovery",
        lambda *a, **k: scheduled.append(load_ending_state(db, "chat", "s1").lifecycle) or True,
    )
    monkeypatch.setattr(
        director_runtime, "queue_director_reassessment", lambda *a, **k: pytest.fail("Planned after resolution")
    )
    director_runtime._post_retain(
        PostRetainContext(
            db,
            "chat",
            session,
            {},
            make_test_provider_port(generate_backend=lambda *a, **k: resolved_packet(rowid, quote)),
            settings,
            make_test_persona_service(),
            make_test_delivery_port(),
        )
    )
    assert scheduled == ["resolution_committed"]


def test_queue_coalesces_duplicate_ending_requests_and_closes_worker_connection(session_db, monkeypatch):
    from bridge import ending_runtime

    settings, db, session = session_db
    resolved(session_db)
    scheduled = []
    monkeypatch.setattr(ending_runtime, "submit_background", lambda *a, **k: scheduled.append((a, k)) or True)
    config = dict(
        provider_port=make_test_provider_port(generate_backend=producer_for(db, [])),
        delivery_port=deliveries(db)[0],
        persona_service=make_test_persona_service(),
        app_settings=settings,
    )
    assert ending_runtime.queue_ending_recovery(db, "chat", session, **config)
    assert not ending_runtime.queue_ending_recovery(db, "chat", session, **config)
    fields = card_fields({"name": "Mara"}, app_settings=settings)
    with patch("bridge.epilogue_service.card_fields_from_file", return_value=fields):
        args, kwargs = scheduled.pop()
        args[1](*args[2:], **kwargs)
    assert load_ending_state(db, "chat", "s1").lifecycle == "closed"
    assert not ending_runtime.queue_ending_recovery(db, "chat", session, **config)


def test_rejected_executor_admission_is_retryable(session_db, monkeypatch):
    from bridge import ending_runtime

    settings, db, session = session_db
    resolved(session_db)
    monkeypatch.setattr(ending_runtime, "submit_background", lambda *a, **k: False)
    config = dict(
        provider_port=make_test_provider_port(),
        delivery_port=make_test_delivery_port(),
        persona_service=make_test_persona_service(),
        app_settings=settings,
    )
    assert not ending_runtime.queue_ending_recovery(db, "chat", session, **config)
    assert not ending_runtime.queue_ending_recovery(db, "chat", session, **config)


def test_startup_scan_is_bounded_and_does_not_call_models_in_the_scanner(session_db, monkeypatch):
    from bridge import ending_runtime

    settings, db, _ = session_db
    resolved(session_db)
    calls = []
    monkeypatch.setattr(ending_runtime, "queue_ending_recovery", lambda *a, **k: calls.append(a[1:3]) or True)
    count = ending_runtime.queue_startup_ending_recovery(
        db,
        provider_port=make_test_provider_port(generate_backend=lambda *a, **k: pytest.fail("Inline startup provider")),
        delivery_port=make_test_delivery_port(),
        persona_service=make_test_persona_service(),
        app_settings=settings,
    )
    assert count == 1 and calls[0][0] == "chat"
    assert not db.in_transaction
