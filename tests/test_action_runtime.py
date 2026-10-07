"""Behavioral tests for action reservation, publication, races and scoped recovery."""

import json

import pytest
from application_test_setup import make_test_provider_port
from test_action_adjudication import proposal
from test_memory_completion_safety import session_db as session_db
from test_simulation_trackers import _assistant_row

from bridge.metadata import set_meta
from bridge.simulation_repository import list_checks
from bridge.sqlite_store import write_transaction


def prepare(case, generate, identity="turn:1", **kwargs):
    from bridge.action_adjudication import prepare_action_turn

    settings, db, session = case
    return prepare_action_turn(
        db,
        "chat",
        session,
        "I quietly open the door.",
        identity,
        provider_port=make_test_provider_port(generate_backend=generate),
        app_settings=settings,
        **kwargs,
    )


def output(db):
    rowid = db.execute("SELECT MAX(id) FROM messages").fetchone()[0]
    return proposal(evidence=[{"source": str(rowid), "quote": "A guard waits beside the noisy door."}])


def test_ready_preflight_survives_retry_without_inference_or_reroll(session_db, monkeypatch):
    from bridge import action_adjudication

    _, db, _ = session_db
    _assistant_row(db, "A guard waits beside the noisy door.")
    calls = []
    monkeypatch.setattr(action_adjudication.secrets, "randbelow", lambda n: calls.append("roll") or 8)

    def generate(*args, **kwargs):
        assert not db.in_transaction
        calls.append("model")
        return output(db)

    first = prepare(session_db, generate)
    again = prepare(session_db, lambda *a, **k: pytest.fail("Retry must reuse a ready proposal"))
    assert first.receipt == again.receipt
    assert first.receipt["roll"] == 9
    assert calls == ["model", "roll"]
    assert list_checks(db, "chat", "s1") == []
    with write_transaction(db):
        first.validate(db)
        source = db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('chat','s1','user',?,1)",
            ("I quietly open the door.",),
        ).lastrowid
        first.bind(db, source)
    assert list_checks(db, "chat", "s1")[0]["roll"] == 9
    assert db.execute("SELECT COUNT(*) FROM action_preflights").fetchone()[0] == 0


@pytest.mark.parametrize("decision", ["no_check", "auto_success"])
def test_no_roll_decisions_do_not_create_check_or_use_rng(session_db, monkeypatch, decision):
    from bridge import action_adjudication

    _, db, _ = session_db
    monkeypatch.setattr(action_adjudication.secrets, "randbelow", lambda n: pytest.fail("No dice warranted"))
    turn = prepare(session_db, lambda *a, **k: json.dumps({"schema_version": 1, "decision": decision}))
    assert turn.receipt["decision"] == decision
    assert turn.messages([{"role": "user", "content": "hello"}]) == [{"role": "user", "content": "hello"}]
    assert list_checks(db, "chat", "s1") == []


def test_history_rewrite_during_preflight_rejects_before_rng(session_db, monkeypatch):
    from bridge import action_adjudication

    _, db, _ = session_db
    _assistant_row(db, "A guard waits beside the noisy door.")
    monkeypatch.setattr(action_adjudication.secrets, "randbelow", lambda n: pytest.fail("Stale source must not roll"))

    def generate(*a, **k):
        raw = output(db)
        with write_transaction(db):
            db.execute("UPDATE messages SET content='The guard left.'")
        return raw

    with pytest.raises(ValueError, match="changed"):
        prepare(session_db, generate)
    assert list_checks(db, "chat", "s1") == []


def test_concurrent_duplicate_cannot_acquire_a_second_roll(session_db, monkeypatch):
    from bridge import action_adjudication

    _, db, _ = session_db
    _assistant_row(db, "A guard waits beside the noisy door.")
    draws = []
    monkeypatch.setattr(action_adjudication.secrets, "randbelow", lambda n: draws.append(n) or 10)

    def generate(*a, **k):
        with pytest.raises(ValueError, match="evaluated"):
            prepare(session_db, lambda *a, **k: pytest.fail("Concurrent duplicate generated"))
        return output(db)

    prepare(session_db, generate)
    assert draws == [20]


def test_private_rationale_is_not_in_mandatory_story_result(session_db):
    _, db, _ = session_db
    _assistant_row(db, "A guard waits beside the noisy door.")
    turn = prepare(session_db, lambda *a, **k: output(db))
    messages = turn.messages([{"role": "user", "content": "I quietly open the door."}])
    assert "Locked action result" in messages[-1]["content"]
    assert "A nearby guard" not in messages[-1]["content"]
    assert "evidence" not in messages[-1]["content"]
    assert "_context_optional" not in messages[-1]


def test_director_mode_uses_selected_director_route(session_db):
    _, db, _ = session_db
    set_meta(db, "action_checks:chat:s1", "director")
    set_meta(db, "task_model:director:chat:s1", "fixture::director")
    calls = []

    def generate(_key, model, messages, **kwargs):
        calls.append((model, kwargs))
        return '{"schema_version":1,"decision":"no_check"}'

    prepare(session_db, generate)
    assert calls[0][0] == "fixture::director"
    assert calls[0][1]["force_non_stream"] is True
    assert calls[0][1]["request_timeout"] == 30.0


def test_user_source_mismatch_rolls_back_receipt_publication(session_db):
    _, db, _ = session_db
    _assistant_row(db, "A guard waits beside the noisy door.")
    turn = prepare(session_db, lambda *a, **k: output(db))
    with pytest.raises(ValueError):
        with write_transaction(db):
            turn.validate(db)
            source = db.execute(
                "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('chat','s1','user','Other',1)"
            ).lastrowid
            turn.bind(db, source)
    assert list_checks(db, "chat", "s1") == []
    assert db.execute("SELECT COUNT(*) FROM action_preflights").fetchone()[0] == 1


def test_manual_mode_and_narrator_steering_skip_provider(session_db):
    from bridge.action_adjudication import prepare_action_turn

    settings, db, session = session_db
    port = make_test_provider_port(generate_backend=lambda *a, **k: pytest.fail("No automatic inference"))
    set_meta(db, "action_checks:chat:s1", "manual")
    assert prepare(session_db, port.generate_backend).receipt is None
    set_meta(db, "action_checks:chat:s1", "auto")
    for text in ("[Narrative steering]\nMove to another scene", "/status", ""):
        assert (
            prepare_action_turn(db, "chat", session, text, "turn:1", provider_port=port, app_settings=settings).receipt
            is None
        )


def test_manual_receipt_is_reused_by_its_following_story_on_regeneration(session_db):
    from bridge.action_adjudication import locked_action_messages
    from bridge.simulation_checks import perform_check

    _, db, _ = session_db
    with write_transaction(db):
        manual = db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) "
            "VALUES('chat','s1','user','Manual check',1)"
        ).lastrowid
    perform_check(
        db,
        "chat",
        "s1",
        request_key="check:manual",
        domain="stealth",
        actor="user",
        action="open",
        dc=15,
        roll=2,
        source_rowid=manual,
    )
    turn = prepare(session_db, lambda *a, **k: pytest.fail("Manual receipt must not be rerolled"))
    assert turn.receipt["roll"] == 2
    with write_transaction(db):
        turn.validate(db)
        narrated = db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('chat','s1','user','Continue',2)"
        ).lastrowid
        turn.bind(db, narrated)
    messages = locked_action_messages(db, "chat", "s1", [], narrated)
    assert messages and '"roll": 2' in str(messages)


def test_unrelated_npc_backlog_does_not_hide_locked_roll_or_latest_prose(session_db):
    from bridge.action_adjudication import locked_action_messages
    from bridge.simulation_checks import perform_check

    _, db, _ = session_db
    first = _assistant_row(db, "A guard waits beside the noisy door.")
    perform_check(
        db,
        "chat",
        "s1",
        request_key="auto:old",
        domain="stealth",
        actor="user",
        action="open",
        dc=15,
        roll=2,
        source_rowid=first,
    )
    with write_transaction(db):
        db.execute("UPDATE memory_layer_state SET covered_id=0 WHERE layer='npc'")
    assert '"roll": 2' in str(locked_action_messages(db, "chat", "s1", [], first))
    turn = prepare(session_db, lambda *a, **k: output(db))
    assert turn.receipt["decision"] == "check"


def test_mode_change_during_inference_cancels_before_rng(session_db, monkeypatch):
    from bridge import action_adjudication

    _, db, _ = session_db
    _assistant_row(db, "A guard waits beside the noisy door.")
    monkeypatch.setattr(action_adjudication.secrets, "randbelow", lambda n: pytest.fail("Mode changed before roll"))

    def generate(*a, **k):
        set_meta(db, "action_checks:chat:s1", "manual")
        return output(db)

    with pytest.raises(ValueError, match="changed"):
        prepare(session_db, generate)


def test_reset_epoch_clears_pending_work_even_when_epoch_key_was_absent(session_db):
    _, db, _ = session_db
    prepare(session_db, lambda *a, **k: '{"schema_version":1,"decision":"no_check"}')
    set_meta(db, "conversation_epoch:chat:s1", "1")
    assert db.execute("SELECT count(*) FROM action_preflights").fetchone()[0] == 0


def test_expired_lease_recovery_does_not_retain_abandoned_worker_authority(session_db, monkeypatch):
    from bridge import action_adjudication

    _, db, _ = session_db
    _assistant_row(db, "A guard waits beside the noisy door.")
    draws = []
    monkeypatch.setattr(action_adjudication.secrets, "randbelow", lambda n: draws.append(n) or 3)

    def interrupted(*a, **k):
        with write_transaction(db):
            db.execute("UPDATE action_preflights SET lease_until=0")
        fresh = prepare(session_db, lambda *a, **k: output(db))
        assert fresh.receipt["roll"] == 4
        return output(db)

    with pytest.raises(ValueError, match="lease changed"):
        prepare(session_db, interrupted)
    assert draws == [20]
    assert prepare(session_db, lambda *a, **k: pytest.fail("Ready result lost")).receipt["roll"] == 4


def test_adjudicator_sees_canonical_domains_but_bridge_calculates_modifier(session_db, monkeypatch):
    from bridge import action_adjudication
    from bridge.simulation_service import SimulationService

    _, db, _ = session_db
    source = _assistant_row(db, "A guard waits beside the noisy door.")
    SimulationService().apply_payload(
        db,
        "chat",
        "s1",
        {"actor": {"skills_add": [{"name": "Quiet entry", "domain": "stealth", "modifier": 2}]}},
        source_rowid=source,
    )
    monkeypatch.setattr(action_adjudication.secrets, "randbelow", lambda n: 9)

    def generate(_key, _model, messages, **kwargs):
        data = json.loads(messages[1]["content"])
        assert data["user_traits"] == [{"name": "Quiet entry", "domain": "stealth"}]
        assert "modifier" not in data["user_traits"][0]
        return output(db)

    turn = prepare(session_db, generate)
    assert turn.receipt["modifier"] == 2 and turn.receipt["roll"] == 10


def test_changing_only_story_model_keeps_an_already_rolled_result(session_db):
    from bridge.session_core import update_session

    _, db, _ = session_db
    _assistant_row(db, "A guard waits beside the noisy door.")
    first = prepare(session_db, lambda *a, **k: output(db))
    update_session(db, "chat", "s1", model_id="fixture::replacement")
    again = prepare(session_db, lambda *a, **k: pytest.fail("Model switch must not reroll"))
    assert again.receipt == first.receipt


def test_invalidated_history_cannot_supply_an_old_locked_result(session_db):
    from bridge.action_adjudication import locked_action_messages
    from bridge.simulation_checks import perform_check

    _, db, _ = session_db
    first = _assistant_row(db, "A guard waits beside the noisy door.")
    perform_check(
        db,
        "chat",
        "s1",
        request_key="auto:old",
        domain="stealth",
        actor="user",
        action="open",
        dc=15,
        roll=2,
        source_rowid=first,
    )
    with write_transaction(db):
        db.execute("UPDATE memory_layer_state SET invalidated_from_id=? WHERE layer='npc'", (first,))
    assert locked_action_messages(db, "chat", "s1", [], first) == []


def test_manual_mode_allows_long_turn_without_automatic_input_limit(session_db):
    from bridge.action_adjudication import prepare_action_turn

    settings, db, session = session_db
    set_meta(db, "action_checks:chat:s1", "manual")
    turn = prepare_action_turn(
        db,
        "chat",
        session,
        "long action " * 2000,
        "telegram:123",
        provider_port=make_test_provider_port(generate_backend=lambda *a, **k: pytest.fail("Manual mode")),
        app_settings=settings,
    )
    assert turn.receipt is None
