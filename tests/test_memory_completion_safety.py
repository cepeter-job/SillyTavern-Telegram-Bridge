"""Behavioral regressions for completion after invalidation and bounded source coverage."""

import json
import threading

import pytest
from application_test_setup import make_test_memory_service, make_test_provider_port
from settings_test_support import make_test_settings

from bridge import group_commands, memory, memory_curator, message_commands
from bridge.memory_backend import hindsight_session_lock
from bridge.metadata import set_meta
from bridge.npc_service import NpcService
from bridge.session_core import create_session, delete_session_data
from bridge.sqlite_store import db_connect, write_transaction


@pytest.fixture
def session_db(tmp_path):
    settings = make_test_settings(home=tmp_path, db_file=tmp_path / "db.sqlite3")
    settings.native_persona_settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings.native_persona_settings_file.write_text(
        json.dumps({"power_user": {"personas": {}, "persona_descriptions": {}}})
    )
    db = db_connect(app_settings=settings)
    session = create_session(db, "chat", "dummy::model", session_id="s1", app_settings=settings)
    yield settings, db, session
    db.close()


def add_rows(db, count, size=2000):
    for number in range(1, count + 1):
        db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", "s1", "user", f"TURN_{number:02d}: " + "x" * size + f" END_{number:02d}", float(number)),
        )
    db.commit()


@pytest.mark.parametrize("change", ["reset", "delete", "recreate", "manual", "transcript", "clear"])
def test_curator_reset_discards_inflight_completion(session_db, monkeypatch, change):
    settings, db, session = session_db
    add_rows(db, 2)
    started = threading.Event()
    release = threading.Event()
    retained = []
    outcomes = []
    errors = []

    def generate(*_args, **_kwargs):
        started.set()
        assert release.wait(5)
        return '{"memories":[{"key":"obsolete","text":"Obsolete fact","kind":"fact"}]}'

    def worker():
        other = db_connect(app_settings=settings)
        try:
            outcomes.append(
                memory_curator.curate_memory_now(
                    other,
                    "",
                    "chat",
                    session,
                    "Alice",
                    provider_port=make_test_provider_port(generate_backend=generate),
                    app_settings=settings,
                )
            )
        except BaseException as error:
            errors.append(error)
        finally:
            other.close()

    monkeypatch.setattr(memory_curator, "_retain_with_client", lambda *a, **k: retained.append(a))
    monkeypatch.setattr(message_commands, "delete_outgoing_messages", lambda *a, **k: None)
    monkeypatch.setattr(message_commands, "delete_incoming_messages", lambda *a, **k: None)
    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert started.wait(5)
        if change == "reset":
            message_commands.reset_session(
                db, "token", "chat", session, memory_service=make_test_memory_service(), npc_service=NpcService()
            )
        elif change in {"delete", "recreate"}:
            assert delete_session_data(db, "chat", "s1", "other", memory_service=make_test_memory_service()) == (
                True,
                "deleted",
            )
            if change == "recreate":
                create_session(db, "chat", "dummy::model", session_id="s1", app_settings=settings)
                add_rows(db, 2)
        elif change == "manual":
            set_meta(
                db,
                memory_curator.memory_curator_key("chat", "s1"),
                json.dumps({"items": [{"key": "manual", "text": "Reviewed fact"}], "through_rowid": 0}),
            )
        elif change == "clear":
            memory_curator.clear_curated_memory_state(db, "chat", "s1")
        else:
            with write_transaction(db):
                db.execute("UPDATE messages SET content='Revised fact' WHERE rowid=1")
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert errors == []
    items, covered = memory_curator.get_curated_memory_state(db, "chat", "s1")
    assert not any(item.get("key") == "obsolete" for item in items)
    assert covered == 0
    assert retained == []
    if change == "manual":
        assert items == [{"key": "manual", "text": "Reviewed fact"}]


def test_curator_acceptance_and_retain_are_ordered_before_concurrent_purge(session_db, monkeypatch):
    settings, db, session = session_db
    add_rows(db, 2)
    accepted = threading.Event()
    attempted = threading.Event()
    events = []
    errors = []
    lock_was_free = []
    original_store = memory_curator._repo_store_meta_value

    def store(connection, key, value):
        original_store(connection, key, value)
        if key == memory_curator.memory_curator_key("chat", "s1"):
            accepted.set()

    def purge_worker():
        other = db_connect(app_settings=settings)
        try:
            assert accepted.wait(5)
            lock = hindsight_session_lock("chat", "s1")
            acquired = lock.acquire(blocking=False)
            lock_was_free.append(acquired)
            if acquired:
                lock.release()
            attempted.set()
            memory.purge_hindsight_session(other, "chat", "s1", app_settings=settings)
        except BaseException as error:
            errors.append(error)
        finally:
            other.close()

    def retain(*_args, **_kwargs):
        assert not db.in_transaction
        assert attempted.wait(5)
        events.append("retain")

    def purge(connection, *_args, **_kwargs):
        assert not connection.in_transaction
        events.append("purge")
        return 1

    monkeypatch.setattr(memory_curator, "_repo_store_meta_value", store)
    monkeypatch.setattr(memory_curator, "_retain_with_client", retain)
    monkeypatch.setattr(memory, "_purge_hindsight_session_backend", purge)
    thread = threading.Thread(target=purge_worker)
    thread.start()
    try:
        memory_curator.curate_memory_now(
            db,
            "",
            "chat",
            session,
            "Alice",
            provider_port=make_test_provider_port(
                generate_backend=lambda *a, **k: '{"memories":[{"key":"current","text":"Current fact","kind":"fact"}]}'
            ),
            app_settings=settings,
        )
    finally:
        thread.join(5)
    assert not thread.is_alive()
    assert errors == []
    assert lock_was_free == [False]
    assert events == ["retain", "purge"]


def test_session_deletion_removes_curated_state_before_session_id_reuse(session_db):
    _settings, db, _session = session_db
    set_meta(
        db,
        memory_curator.memory_curator_key("chat", "s1"),
        json.dumps({"items": [{"key": "old", "text": "Old fact"}], "through_rowid": 10}),
    )
    assert delete_session_data(db, "chat", "s1", "other", memory_service=make_test_memory_service()) == (
        True,
        "deleted",
    )
    assert memory_curator.get_curated_memory_state(db, "chat", "s1") == ([], 0)


@pytest.mark.parametrize("force", [True, False])
def test_summary_coverage_stops_at_processed_rows(session_db, force):
    settings, db, session = session_db
    add_rows(db, 40 if force else 64)
    prompts = []

    def generate(_key, _model, messages, **_kwargs):
        if str(_kwargs.get("session_id", "")).startswith("episodic:"):
            return "[]"
        assert not db.in_transaction
        prompts.append(messages[-1]["content"])
        return "Summary of supplied complete rows."

    memory.generate_session_summary(
        db,
        "chat",
        session,
        force=force,
        provider_port=make_test_provider_port(generate_backend=generate),
        app_settings=settings,
    )
    summary, covered = memory.get_session_summary(db, "chat", "s1")
    assert summary
    assert covered == 40
    assert all(f"END_{n:02d}" in "\n".join(prompts) for n in range(1, 41))
    assert len(prompts) >= 2
    assert all(len(prompt.split("\n\n", 1)[1]) <= 50000 for prompt in prompts)
    assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == (40 if force else 64)


def test_summary_prior_summary_overhead_preserves_whole_rows(session_db):
    settings, db, session = session_db
    add_rows(db, 64)
    db.execute("INSERT INTO session_summaries VALUES(?,?,?,?,?)", ("chat", "s1", "prior " * 2000, 1, 1.0))
    db.commit()
    prompts = []

    def generate(_key, _model, messages, **_kwargs):
        if str(_kwargs.get("session_id", "")).startswith("episodic:"):
            return "[]"
        prompts.append(messages[-1]["content"])
        return "Updated continuity."

    memory.generate_session_summary(
        db, "chat", session, provider_port=make_test_provider_port(generate_backend=generate), app_settings=settings
    )
    assert memory.get_session_summary(db, "chat", "s1")[1] == 40
    assert "prior " in prompts[0]
    assert all(f"END_{n:02d}" in "\n".join(prompts) for n in range(2, 41))
    assert all(len(prompt.split("\n\n", 1)[1]) <= 50000 for prompt in prompts)


def test_summary_oversized_row_is_not_marked_covered(session_db):
    settings, db, session = session_db
    add_rows(db, 1, size=51000)
    prompts = []
    result = memory.generate_session_summary(
        db,
        "chat",
        session,
        force=True,
        provider_port=make_test_provider_port(generate_backend=lambda *a, **k: prompts.append(a) or "Truncated"),
        app_settings=settings,
    )
    assert memory.get_session_summary(db, "chat", "s1") == ("", 0)
    assert result == ""
    assert prompts == []
    assert len(db.execute("SELECT content FROM messages").fetchone()[0]) > 51000


def test_summary_later_segment_failure_preserves_only_completed_coverage(session_db):
    settings, db, session = session_db
    add_rows(db, 40)
    prompts = []

    def generate(_key, _model, messages, **_kwargs):
        if str(_kwargs.get("session_id", "")).startswith("episodic:"):
            return "[]"
        prompts.append(messages[-1]["content"])
        if len(prompts) == 2:
            raise RuntimeError("synthetic later segment failure")
        return "Completed prefix."

    result = memory.generate_session_summary(
        db,
        "chat",
        session,
        force=True,
        provider_port=make_test_provider_port(generate_backend=generate),
        app_settings=settings,
    )
    assert result == "Completed prefix."
    summary, covered = memory.get_session_summary(db, "chat", "s1")
    assert summary == result
    assert 0 < covered < 40
    assert f"END_{covered:02d}" in prompts[0]
    assert f"TURN_{covered + 1:02d}" not in prompts[0]
    assert len(prompts) == 2
    assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 40


@pytest.mark.parametrize("failure", ["later", "oversize", "old"])
def test_summary_command_reports_incomplete_regeneration(session_db, monkeypatch, failure):
    settings, db, session = session_db
    add_rows(db, 40 if failure == "later" else 1, size=51000 if failure == "oversize" else 2000)
    if failure == "old":
        db.execute("INSERT INTO session_summaries VALUES(?,?,?,?,?)", ("chat", "s1", "Prior summary", 1, 1.0))
        db.commit()
    calls = []
    sent = []

    def generate(*_args, **_kwargs):
        calls.append(True)
        if failure == "old" or len(calls) == 2:
            raise RuntimeError("synthetic provider failure")
        return "Processed prefix"

    monkeypatch.setattr(group_commands, "send_typing", lambda *a, **k: None)
    monkeypatch.setattr(group_commands, "send_text", lambda token, chat_id, text: sent.append(text))
    group_commands.handle_summary_command(
        db,
        "token",
        "chat",
        session,
        provider_port=make_test_provider_port(generate_backend=generate),
        app_settings=settings,
    )
    assert len(sent) == 1
    assert "incomplete" in sent[0].casefold()
    assert "Session summary updated" not in sent[0]
    if failure == "old":
        assert "Prior summary" in sent[0]
    elif failure == "later":
        assert "Processed prefix" in sent[0]
