"""Durable action receipts survive retries without changing their odds or result."""

import time

import pytest
from test_simulation_trackers import _assistant_row, _db

from bridge.sqlite_store import write_transaction


@pytest.fixture
def db():
    connection = _db()
    _assistant_row(connection, "A guard waits beside the noisy door.")
    yield connection
    connection.close()


def reserve(db, **overrides):
    from bridge.action_repository import reserve_action

    args = dict(request_key="message:42", actor_id="player", action="I try to open the door quietly.")
    return reserve_action(db, "chat", "s1", **(args | overrides))


def check():
    return {"decision": "check", "domain": "stealth", "dc": 13}


def test_accepted_roll_is_saved_before_story_and_reused_on_retry(db, monkeypatch):
    from bridge import action_repository as repo

    calls = []
    monkeypatch.setattr(repo.secrets, "randbelow", lambda size: calls.append(size) or 11)
    ticket = reserve(db)
    accepted = repo.accept_action(db, ticket, check())
    assert accepted["result"]["roll"] == 12
    assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    again = reserve(db)
    assert again["result"] == accepted["result"]
    assert calls == [20]


def test_second_inflight_reservation_cannot_adjudicate_same_action(db):
    reserve(db)
    with pytest.raises(ValueError, match="progress"):
        reserve(db)


@pytest.mark.parametrize("mutation", ["append", "rewrite", "delete", "reset", "actor"])
def test_changed_owner_or_story_cannot_accept_old_proposal(db, mutation):
    from bridge import action_repository as repo

    ticket = reserve(db)
    if mutation == "append":
        _assistant_row(db, "A later scene.")
    elif mutation == "rewrite":
        db.execute("UPDATE messages SET content='No guard.'")
        db.commit()
    elif mutation == "delete":
        db.execute("DELETE FROM sessions WHERE chat_id='chat' AND session_id='s1'")
        db.commit()
    elif mutation == "reset":
        from bridge.conversation_lifecycle import reset_conversation

        reset_conversation(db, "chat", "s1")
    else:
        ticket = ticket | {"actor_id": "someone-else"}
    with pytest.raises(ValueError):
        repo.accept_action(db, ticket, check())
    assert db.execute("SELECT COUNT(*) FROM simulation_checks").fetchone()[0] == 0


def test_binding_is_atomic_and_uses_exact_committed_user_source(db):
    from bridge import action_repository as repo

    ticket = repo.accept_action(db, reserve(db), check())
    with write_transaction(db):
        repo.validate_action(db, ticket)
        source = db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('chat','s1','user',?,?)",
            (ticket["action"], time.time()),
        ).lastrowid
        repo.bind_action(db, ticket, source)
    saved = db.execute("SELECT source_rowid,roll,modifier FROM simulation_checks").fetchone()
    assert saved == (source, ticket["result"]["roll"], ticket["result"]["modifier"])
    assert repo.committed_action_context(db, "chat", "s1", source)
    db.execute("UPDATE messages SET content='A different action.' WHERE id=?", (source,))
    db.commit()
    assert repo.committed_action_context(db, "chat", "s1", source) == ""


def test_no_check_does_not_roll_and_wrong_source_cannot_bind(db, monkeypatch):
    from bridge import action_repository as repo

    monkeypatch.setattr(repo.secrets, "randbelow", lambda *_: pytest.fail("Routine action rolled"))
    ticket = repo.accept_action(db, reserve(db), {"decision": "no_check"})
    assert ticket["result"]["decision"] == "no_check"
    with pytest.raises(ValueError), write_transaction(db):
        repo.bind_action(db, ticket, 1)
    assert db.execute("SELECT COUNT(*) FROM simulation_checks").fetchone()[0] == 0


def test_retry_cannot_change_action_identity(db):
    from bridge import action_repository as repo

    repo.accept_action(db, reserve(db), check())
    with pytest.raises(ValueError):
        reserve(db, actor_id="other-player")
    with pytest.raises(ValueError):
        reserve(db, action="I force the door.")


def test_expired_pending_lease_is_recoverable_without_accepting_old_worker(db):
    from bridge import action_repository as repo

    first = reserve(db)
    db.execute("UPDATE action_attempts SET lease_until=0")
    db.commit()
    second = reserve(db)
    assert first["lease_token"] != second["lease_token"]
    with pytest.raises(ValueError):
        repo.accept_action(db, first, check())
    assert repo.accept_action(db, second, check())["result"]["decision"] == "check"
