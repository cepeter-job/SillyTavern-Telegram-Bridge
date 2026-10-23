"""Adjudication failures retain recovery details without accepting an action."""

import pytest
from application_test_setup import make_test_provider_port
from session_test_support import session_db as session_db

from bridge.action_adjudication import prepare_action_turn, set_action_mode
from bridge.provider_errors import ProviderRequestError
from bridge.simulation_repository import list_checks


def _prepare(case, generate, *, action="I quietly open the door."):
    settings, db, session = case
    return prepare_action_turn(
        db,
        "chat",
        session,
        action,
        "telegram:101",
        provider_port=make_test_provider_port(generate_backend=generate),
        app_settings=settings,
    )


def _assert_no_action_accepted(db, draws):
    assert draws == []
    assert list_checks(db, "chat", "s1") == []
    assert db.execute("SELECT COUNT(*) FROM action_preflights").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    assert not db.in_transaction


@pytest.mark.parametrize("blocked", [False, True])
def test_adjudication_preserves_rate_limit_details_and_releases_preflight(session_db, monkeypatch, blocked):
    from bridge import action_adjudication

    _, db, _ = session_db
    draws = []
    monkeypatch.setattr(action_adjudication.secrets, "randbelow", lambda n: draws.append(n) or 0)
    error = ProviderRequestError("fixture::adjudicator", "rate_limit", 429, retry_after=37, blocked=blocked)

    def unavailable(*args, **kwargs):
        assert not db.in_transaction
        raise error

    with pytest.raises(ProviderRequestError) as raised:
        _prepare(session_db, unavailable)

    assert raised.value is error
    assert (error.model, error.category, error.status, error.retry_after, error.blocked) == (
        "fixture::adjudicator",
        "rate_limit",
        429,
        37,
        blocked,
    )
    _assert_no_action_accepted(db, draws)
    retry = _prepare(session_db, lambda *a, **k: '{"schema_version":1,"decision":"no_check"}')
    assert retry.receipt["decision"] == "no_check"
    assert draws == []


def test_unexpected_adjudication_failure_keeps_sanitized_manual_panel_guidance(session_db, monkeypatch):
    from bridge import action_adjudication

    _, db, _ = session_db
    draws = []
    monkeypatch.setattr(action_adjudication.secrets, "randbelow", lambda n: draws.append(n) or 0)

    def unavailable(*args, **kwargs):
        raise RuntimeError("private upstream detail")

    with pytest.raises(ValueError, match="open /check and choose Manual") as raised:
        _prepare(session_db, unavailable)

    assert "private" not in str(raised.value)
    _assert_no_action_accepted(db, draws)


def test_oversized_action_points_to_manual_panel_before_inference(session_db):
    with pytest.raises(ValueError, match="open /check and choose Manual"):
        _prepare(session_db, lambda *a, **k: pytest.fail("Oversized input called provider"), action="x" * 12001)
    _assert_no_action_accepted(session_db[1], [])


def test_invalid_action_mode_points_to_available_panel_controls(session_db):
    with pytest.raises(ValueError, match="Open /check and choose Auto, Director, or Manual"):
        set_action_mode(session_db[1], "chat", "s1", "invalid")
