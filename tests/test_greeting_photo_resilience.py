"""Optional opening media never invalidates acknowledged story text."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.error import URLError

import pytest
from test_light_novel_storage import novel_db as novel_db

import bridge.response_delivery as delivery
from bridge import greetings
from bridge.conversation_lifecycle import configure_conversation, conversation_state
from bridge.diagnostic_logging import DiagnosticFormatter
from bridge.operations import operation_phase
from bridge.sqlite_store import db_connect, write_transaction


def opening_transport(novel_db, monkeypatch, photo, *, local=False):
    db, _, settings = novel_db
    sent = []
    configure_conversation(db, "chat", "story", "lightnovel", "c")

    def text(_token, _chat, content, *, acknowledged_chunk=None, **kwargs):
        sent.append(content)
        acknowledged_chunk(71)
        return [71]

    monkeypatch.setattr(delivery, "send_text", text)
    fields = {"name": "Alice", "first_mes": "Hello ![portrait](https://images.example/private.png)"}
    if local:
        fields["first_mes"] = "Hello"
        path = settings.character_dir / "Alice.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"local transport fixture")
        with write_transaction(db):
            db.execute("UPDATE sessions SET character_file='Alice.png' WHERE session_id='story'")
        monkeypatch.setattr(greetings, "send_panel_photo", photo)
    else:
        monkeypatch.setattr(greetings, "telegram_request", photo)
    return fields, sent


@pytest.mark.parametrize("local", [False, True])
@pytest.mark.parametrize(
    "failure", [URLError("PRIVATE_RESET"), TimeoutError("PRIVATE_TIMEOUT"), RuntimeError("PRIVATE_API")]
)
def test_photo_transport_failure_preserves_started_opening_and_choices(novel_db, monkeypatch, caplog, local, failure):
    db, _, settings = novel_db
    attempts = []

    def photo(*args, **kwargs):
        assert not db.in_transaction
        attempts.append(1)
        raise failure

    fields, sent = opening_transport(novel_db, monkeypatch, photo, local=local)
    assert greetings.send_character_greeting(
        db, "token", "chat", fields, "story", "User", operation_id=41, app_settings=settings
    )
    assert conversation_state(db, "chat", "story").started
    assert operation_phase(db, 41) == "applied"
    assert len(sent) == 1
    assert db.execute("SELECT telegram_message_ids FROM messages").fetchall() == [("[71]",)]
    assert db.execute("SELECT generation_status FROM light_novel_choice_sets").fetchall() == [("pending",)]
    assert not greetings.send_character_greeting(
        db, "token", "chat", fields, "story", "User", operation_id=41, app_settings=settings
    )
    assert attempts == [1]
    failures = [getattr(r, "diagnostic_fields", {}) for r in caplog.records]
    assert any(r.get("event") == "greeting.photo_failed" and r.get("status") == "skipped" for r in failures)
    rendered = "\n".join(DiagnosticFormatter().format(r) for r in caplog.records)
    assert "PRIVATE_" not in rendered and "images.example" not in rendered


def test_interrupted_photo_is_not_retransmitted_after_database_reopen(novel_db, monkeypatch):
    db, _, settings = novel_db

    class ProcessInterrupted(BaseException):
        pass

    def interrupted(*args, **kwargs):
        assert not db.in_transaction
        raise ProcessInterrupted()

    fields, sent = opening_transport(novel_db, monkeypatch, interrupted)
    with pytest.raises(ProcessInterrupted):
        greetings.send_character_greeting(
            db, "token", "chat", fields, "story", "User", operation_id=41, app_settings=settings
        )
    assert operation_phase(db, 41) == "local_committed"
    monkeypatch.setattr(greetings, "telegram_request", lambda *a, **kw: pytest.fail("uncertain photo retransmitted"))
    database_path = Path(db.execute("PRAGMA database_list").fetchone()[2])
    with closing(db_connect(database_path, app_settings=settings)) as reopened:
        assert greetings.send_character_greeting(
            reopened, "token", "chat", fields, "story", "User", operation_id=41, app_settings=settings
        )
        assert operation_phase(reopened, 41) == "applied"
        assert reopened.execute("SELECT COUNT(*) FROM messages").fetchone() == (1,)
    assert len(sent) == 1


def test_photo_receipt_database_failure_is_not_treated_as_transport_failure(novel_db, monkeypatch):
    db, _, settings = novel_db
    fields, _ = opening_transport(novel_db, monkeypatch, lambda *a, **kw: {"message_id": 72})
    original = greetings.set_meta

    def broken_receipt(connection, key, value):
        if key.startswith("greeting_photo:") and value == "72":
            raise sqlite3.OperationalError("receipt database unavailable")
        return original(connection, key, value)

    monkeypatch.setattr(greetings, "set_meta", broken_receipt)
    with pytest.raises(sqlite3.OperationalError, match="receipt database unavailable"):
        greetings.send_character_greeting(
            db, "token", "chat", fields, "story", "User", operation_id=41, app_settings=settings
        )
    assert operation_phase(db, 41) == "local_committed"
    assert json.loads(db.execute("SELECT telegram_message_ids FROM messages").fetchone()[0]) == [71]
