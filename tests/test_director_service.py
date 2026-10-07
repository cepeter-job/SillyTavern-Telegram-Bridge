"""Director proposals are measured, leased, and never published from stale facts."""

import json

import pytest
from application_test_setup import make_test_provider_port
from test_memory_completion_safety import session_db as session_db
from test_narrative_reconciliation import add_story, run_reconciliation

from bridge.director_guidance import director_guidance_for_session
from bridge.director_repository import append_director_decision, load_director_state
from bridge.director_service import DirectorService
from bridge.model_selection import set_director_reasoning, set_task_model
from bridge.narrative_repository import load_narrative_clock
from bridge.narrative_settings import preset_narrative_settings, save_session_narrative_settings
from bridge.sqlite_store import write_transaction


@pytest.fixture
def directed(session_db):
    _settings, db, _session = session_db
    save_session_narrative_settings(db, "chat", "s1", preset_narrative_settings("world_driven"))
    add_story(db)
    run_reconciliation(session_db)
    return session_db


def response(db, **changes):
    return json.dumps(
        {
            "schema_version": 1,
            "action": "continue",
            "scene_id": "gate",
            "thread_id": "rebellion",
            "expected_revision": load_narrative_clock(db, "chat", "s1")["state_revision"],
            "direction": "Keep pressure on the gate.",
            "direction_ttl": 4,
        }
        | changes
    )


def reassess(directed, generate=None, **kwargs):
    settings, db, session = directed
    return DirectorService().reassess(
        db,
        "",
        "chat",
        session,
        reason="manual",
        provider_port=make_test_provider_port(generate_backend=generate or (lambda *a, **k: response(db))),
        app_settings=settings,
        valid_characters={"Mara", "Governor"},
        user_characters={"Alex"},
        **kwargs,
    )


def test_director_ensures_current_facts_before_call_and_uses_independent_route(directed, monkeypatch):
    from bridge import director_service

    _settings, db, _session = directed
    calls = []
    original = director_service.ensure_narrative_state_current

    def reconcile(*a, **kw):
        calls.append("reconcile")
        return original(*a, **kw)

    monkeypatch.setattr(director_service, "ensure_narrative_state_current", reconcile)
    set_task_model(db, "chat", "s1", "utility::worker")
    set_task_model(db, "chat", "s1", "director::planner", "director")
    set_director_reasoning(db, "chat", "s1", 8192)

    def generate(_key, model, messages, **kw):
        assert calls == ["reconcile"]
        assert not db.in_transaction
        assert model == "director::planner"
        assert kw["settings"]["reasoning_budget"] == 8192
        assert kw["force_non_stream"] is True
        assert "committed" in messages[0]["content"]
        calls.append("director")
        return response(db)

    result = reassess(directed, generate)
    assert result.result == "accepted"
    assert result.decision_id is not None
    assert load_director_state(db, "chat", "s1")["active_direction"] == "Keep pressure on the gate."
    assert db.execute("SELECT COUNT(*) FROM director_decisions").fetchone()[0] == 1
    assert load_narrative_clock(db, "chat", "s1")["state_revision"] == result.expected_revision


def test_transition_is_stored_only_as_a_plan(directed):
    _settings, db, _session = directed
    result = reassess(
        directed,
        lambda *a, **k: response(
            db,
            action="transition_scene",
            thread_id="palace",
            new_thread={"thread_id": "palace", "title": "Palace"},
            viewpoint="Governor",
            pov="third_person_rotating",
            user_present=False,
            transition_type="cut",
        ),
    )
    assert result.result == "accepted"
    assert db.execute("SELECT active_scene_id,active_thread_id FROM narrative_state").fetchone() == (
        "gate",
        "rebellion",
    )
    assert db.execute("SELECT 1 FROM narrative_threads WHERE thread_id='palace'").fetchone() is None
    assert "palace" in load_director_state(db, "chat", "s1")["active_proposal_json"]


@pytest.mark.parametrize("raw,expected_calls", [("broken JSON", 2), ('{"schema_version":2}', 1), ("{}", 1)])
def test_repair_is_bounded_and_unsupported_versions_are_not_repaired(directed, raw, expected_calls):
    _settings, db, _session = directed
    calls = []

    def generate(*args, **kwargs):
        calls.append(1)
        return raw

    result = reassess(directed, generate)
    assert result.result == "rejected"
    assert len(calls) == expected_calls
    assert load_director_state(db, "chat", "s1")["degraded_state"]
    assert load_director_state(db, "chat", "s1")["active_direction"] == ""
    assert load_director_state(db, "chat", "s1")["inflight_token"] == ""


def test_repair_prompt_is_specific_and_failure_log_stays_sanitized(directed, caplog):
    _settings, db, _session = directed
    invalid = response(db, direction={"PRIVATE_RAW": "secret"})
    calls = []

    def generate(_key, _model, messages, **_kwargs):
        calls.append([dict(message) for message in messages])
        return invalid

    result = reassess(directed, generate)
    assert result.result == "rejected"
    assert len(calls) == 2
    repair = calls[1][-1]["content"]
    assert "field 'direction'" in repair
    assert "Omit optional string fields instead of null" in repair
    assert "Do not use Markdown or code fences" in repair
    assert "field 'direction'" in caplog.text
    assert "PRIVATE_RAW" not in caplog.text


def test_one_repair_can_produce_one_valid_decision(directed):
    _settings, db, _session = directed
    responses = iter(['{"schema_version":1,"action":', response(db)])
    assert reassess(directed, lambda *a, **k: next(responses)).result == "accepted"
    assert db.execute("SELECT COUNT(*) FROM director_decisions").fetchone()[0] == 1


@pytest.mark.parametrize("mutation", ["append", "edit", "style", "manual", "delete"])
def test_late_proposal_never_overwrites_newer_reality_or_manual_intent(directed, mutation):
    _settings, db, _session = directed
    from bridge.director_goals import set_director_goal

    def generate(*args, **kwargs):
        raw = response(db, direction="OBSOLETE")
        if mutation == "append":
            add_story(db, "The gate opens.")
        elif mutation == "style":
            save_session_narrative_settings(db, "chat", "s1", preset_narrative_settings("observer"))
        elif mutation == "manual":
            set_director_goal(db, "chat", "s1", "Manual objective wins")
        else:
            with write_transaction(db):
                if mutation == "edit":
                    db.execute("UPDATE messages SET content='There never was a gate.'")
                else:
                    db.execute("DELETE FROM sessions WHERE chat_id='chat' AND session_id='s1'")
        return raw

    result = reassess(directed, generate)
    assert result.result != "accepted"
    assert load_director_state(db, "chat", "s1")["active_direction"] != "OBSOLETE"
    if mutation == "manual":
        assert load_director_state(db, "chat", "s1")["goal"] == "Manual objective wins"


def test_failure_preserves_accepted_direction_and_does_not_expose_raw_error(directed, caplog):
    _settings, db, _session = directed
    assert reassess(directed).result == "accepted"

    def unavailable(*args, **kwargs):
        raise TimeoutError("PRIVATE_KEY diagnostic")

    result = reassess(directed, unavailable)
    current = load_director_state(db, "chat", "s1")
    assert result.result == "rejected"
    assert current["active_direction"] == "Keep pressure on the gate."
    assert current["degraded_state"]
    assert "PRIVATE_KEY" not in result.reason
    assert "PRIVATE_KEY" not in caplog.text


def test_reentrant_reassess_is_single_flight(directed):
    _settings, db, _session = directed
    calls = []

    def generate(*a, **k):
        calls.append(1)
        inner = reassess(directed, lambda *a, **k: pytest.fail("duplicate admitted Director request"))
        assert inner.result == "rejected"
        return response(db)

    assert reassess(directed, generate).result == "accepted"
    assert calls == [1]


def test_provider_request_is_forbidden_inside_caller_transaction(directed):
    _settings, db, _session = directed
    db.execute("BEGIN")
    try:
        with pytest.raises(RuntimeError, match="transaction"):
            reassess(directed, lambda *a, **k: pytest.fail("provider called under transaction"))
    finally:
        db.rollback()


def test_history_is_bounded_and_cannot_delete_current_direction(directed):
    _settings, db, _session = directed
    reassess(directed)
    with pytest.raises(RuntimeError, match="transaction"):
        append_director_decision(
            db,
            "chat",
            "s1",
            source="ai",
            result="rejected",
            expected_revision=1,
            proposal_json="{}",
            accepted_direction="",
            reason="fixture",
            created_at=1,
        )
    with write_transaction(db):
        for index in range(210):
            append_director_decision(
                db,
                "chat",
                "s1",
                source="ai",
                result="rejected",
                expected_revision=1,
                proposal_json="{}",
                accepted_direction="",
                reason="fixture",
                created_at=index,
            )
    assert db.execute("SELECT COUNT(*) FROM director_decisions").fetchone()[0] == 200
    assert load_director_state(db, "chat", "s1")["active_direction"] == "Keep pressure on the gate."


def test_rewrite_clock_survives_reconciliation_and_invalidates_cached_direction(directed):
    _settings, db, _session = directed
    reassess(directed)
    before = load_narrative_clock(db, "chat", "s1")["rewrite_revision"]
    assert "Keep pressure" in director_guidance_for_session(db, "chat", "s1")
    add_story(db, "Mara still waits.")
    run_reconciliation(directed)
    assert load_narrative_clock(db, "chat", "s1")["rewrite_revision"] == before
    assert "Keep pressure" in director_guidance_for_session(db, "chat", "s1")
    with write_transaction(db):
        db.execute("UPDATE messages SET content='Mara left the gate.' WHERE id=(SELECT MIN(id) FROM messages)")
    run_reconciliation(directed)
    assert load_narrative_clock(db, "chat", "s1")["rewrite_revision"] > before
    assert "Keep pressure" not in director_guidance_for_session(db, "chat", "s1")


def test_persistent_manual_objective_is_canonical_and_survives_ai_reassessment(directed):
    _settings, db, _session = directed
    revision = load_narrative_clock(db, "chat", "s1")["state_revision"]
    result = DirectorService().accept_manual_direction(
        db,
        "chat",
        "s1",
        direction="Keep Mara independent.",
        scope="persistent",
        expected_revision=revision,
        expected_director_revision=0,
    )
    assert result.result == "accepted"
    assert load_director_state(db, "chat", "s1")["goal"] == "Keep Mara independent."
    reassess(directed)
    assert load_director_state(db, "chat", "s1")["goal"] == "Keep Mara independent."
    assert "Keep Mara independent" in director_guidance_for_session(db, "chat", "s1")


def test_stale_manual_panel_revision_cannot_overwrite_newer_manual_direction(directed):
    _settings, db, _session = directed
    revision = load_narrative_clock(db, "chat", "s1")["state_revision"]
    service = DirectorService()
    assert (
        service.accept_manual_direction(
            db,
            "chat",
            "s1",
            direction="First",
            scope="persistent",
            expected_revision=revision,
            expected_director_revision=0,
        ).result
        == "accepted"
    )
    result = service.accept_manual_direction(
        db,
        "chat",
        "s1",
        direction="Stale second",
        scope="persistent",
        expected_revision=revision,
        expected_director_revision=0,
    )
    assert result.result == "rejected"
    assert load_director_state(db, "chat", "s1")["goal"] == "First"


def test_closed_story_has_no_director_request_or_state_write(directed):
    _settings, db, _session = directed
    with write_transaction(db):
        db.execute("INSERT INTO ending_state(chat_id,session_id,lifecycle) VALUES('chat','s1','closed')")
    before = db.total_changes
    result = reassess(directed, lambda *a, **k: pytest.fail("closed Director provider request"))
    assert result.result == "rejected"
    assert db.total_changes == before


@pytest.mark.parametrize("change", ["close", "manual"])
def test_reconciliation_cannot_be_followed_by_a_superseded_provider_request(directed, monkeypatch, change):
    from bridge import director_service

    _settings, db, _session = directed
    original = director_service.ensure_narrative_state_current

    def supersede(*a, **kw):
        current = original(*a, **kw)
        if change == "close":
            with write_transaction(db):
                db.execute("INSERT INTO ending_state(chat_id,session_id,lifecycle) VALUES('chat','s1','closed')")
        else:
            DirectorService().accept_manual_direction(
                db,
                "chat",
                "s1",
                direction="New objective",
                scope="persistent",
                expected_revision=current.state_revision,
                expected_director_revision=load_director_state(db, "chat", "s1")["state_revision"],
            )
        return current

    monkeypatch.setattr(director_service, "ensure_narrative_state_current", supersede)
    result = reassess(directed, lambda *a, **k: pytest.fail("superseded operation called provider"))
    assert result.result == "rejected"


def test_one_scene_manual_direction_requires_current_scene_facts(directed):
    _settings, db, _session = directed
    add_story(db, "A new scene that has not been reconciled.")
    revision = load_narrative_clock(db, "chat", "s1")["state_revision"]
    result = DirectorService().accept_manual_direction(
        db,
        "chat",
        "s1",
        direction="Stay here.",
        scope="next_scene",
        expected_revision=revision,
        expected_director_revision=0,
    )
    assert result.result == "rejected"
    assert load_director_state(db, "chat", "s1")["active_direction"] == ""


def test_append_after_reconciliation_does_not_turn_old_state_into_a_fresh_snapshot(directed, monkeypatch):
    from bridge import director_service

    original = director_service.ensure_narrative_state_current
    _settings, db, _session = directed

    def append_after(*args, **kwargs):
        result = original(*args, **kwargs)
        add_story(db, "A concurrent committed scene.")
        return result

    monkeypatch.setattr(director_service, "ensure_narrative_state_current", append_after)
    result = reassess(directed, lambda *a, **k: pytest.fail("outdated state treated as fresh"))
    assert result.result == "rejected"


def test_stale_first_response_is_not_repaired_against_changed_story(directed):
    _settings, db, _session = directed
    calls = []

    def generate(*args, **kwargs):
        calls.append(1)
        if len(calls) > 1:
            pytest.fail("repair used an obsolete story snapshot")
        add_story(db, "A new event supersedes the original planning request.")
        return "malformed"

    assert reassess(directed, generate).result == "rejected"
    assert calls == [1]


def test_one_scene_override_cannot_silently_attach_to_no_scene(session_db):
    _config, db, session = session_db
    result = DirectorService().accept_manual_direction(
        db,
        "chat",
        session["session_id"],
        direction="Stay at the gate.",
        scope="next_scene",
        expected_revision=0,
        expected_director_revision=0,
    )
    assert result.result == "rejected"


def test_unstarted_story_does_not_make_an_automatic_director_request(session_db):
    result = reassess(session_db, lambda *a, **k: pytest.fail("unstarted story contacted Director"))
    assert result.result == "rejected"


def test_proposed_new_thread_cannot_collide_with_an_older_thread_outside_context_window(directed):
    _settings, db, _session = directed
    with write_transaction(db):
        for number in range(70):
            db.execute(
                "INSERT INTO narrative_threads(chat_id,session_id,thread_id,title,source_revision) VALUES(?,?,?,?,?)",
                ("chat", "s1", f"older_{number}", "Older thread", number),
            )
    result = reassess(
        directed,
        lambda *a, **k: response(
            db,
            action="transition_scene",
            thread_id="older_0",
            new_thread={"thread_id": "older_0", "title": "Duplicate"},
            viewpoint="Mara",
            pov="third_person_rotating",
            user_present=False,
            transition_type="cut",
        ),
    )
    assert result.result == "rejected"
    assert db.execute("SELECT title FROM narrative_threads WHERE thread_id='older_0'").fetchone() == ("Older thread",)
