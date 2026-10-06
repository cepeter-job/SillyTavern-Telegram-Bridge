"""Real transcript and delivery state regressions with external transport intercepted."""

import json
import sqlite3
from urllib.error import URLError

import npc_test_support as ns
import pytest
from application_test_setup import (
    make_test_memory_service,
    make_test_persona_service,
    make_test_provider_port,
    make_test_rag_service,
)
from settings_test_support import SettingsBuilder

from bridge import edit_messages, response_delivery, schema, telegram
from bridge.migrations import run_migrations
from bridge.npc_service import NpcService
from bridge.response_variants import keep_swipe_variant, last_user_variants, save_response_variant


def test_edit_prunes_discarded_prompt_alternatives_before_unrelated_turn(monkeypatch):
    db = ns.db()
    try:
        target = ns.turn(db, "user", "old prompt", 1)
        ns.turn(db, "assistant", "old answer", 2)
        discarded = ns.turn(db, "user", "discarded prompt", 3)
        highest = ns.turn(db, "assistant", "discarded answer", 4)
        db.commit()
        save_response_variant(db, "chat", "s1", "old prompt", "old alternative", target)
        save_response_variant(db, "chat", "s1", "discarded prompt", "discarded alternative", discarded)
        monkeypatch.setattr(edit_messages, "send_typing", lambda *a: None)
        monkeypatch.setattr(edit_messages, "send_reply", lambda *a, **k: None)
        edit_messages.regenerate_edited_turn(
            db,
            "token",
            "key",
            ns.session(),
            ns.fields(),
            "chat",
            target,
            "edited prompt",
            provider_port=make_test_provider_port(generate_backend=lambda *a, **k: "edited answer"),
            memory_service=make_test_memory_service(),
            npc_service=NpcService(),
            persona_service=make_test_persona_service(),
            app_settings=SettingsBuilder().build(),
            rag_service=make_test_rag_service(),
        )
        assert db.execute("SELECT user_content FROM response_variants").fetchall() == [("edited prompt",)]
        unrelated = ns.turn(db, "user", "unrelated prompt", 5)
        db.commit()
        assert unrelated > highest
        assert last_user_variants(db, "chat", "s1") == ((unrelated, "unrelated prompt"), [])
        assert keep_swipe_variant(db, "chat", "s1", 1, npc_service=NpcService()) is None
        save_response_variant(db, "chat", "s1", "unrelated prompt", "new answer", unrelated)
        assert keep_swipe_variant(db, "chat", "s1", 1, npc_service=NpcService()) == "new answer"
    finally:
        db.close()


def test_populated_identity_migration_preserves_rowids_indexes_and_references():
    db = sqlite3.connect(":memory:")
    try:
        run_migrations(db, schema.SCHEMA_MIGRATIONS[:6])
        db.execute(
            "INSERT INTO messages(rowid,chat_id,session_id,role,content,telegram_message_id,"
            "telegram_message_ids,created_at) "
            "VALUES(91,'c','s','user','content','73','[74]',1)"
        )
        db.execute(
            "INSERT INTO response_variants(chat_id,session_id,user_rowid,user_content,response,"
            "variant_index,created_at) "
            "VALUES('c','s',91,'content','answer',1,1)"
        )
        before = db.execute("SELECT rowid,* FROM messages").fetchall()
        indexes = db.execute("SELECT name,sql FROM sqlite_master WHERE type='index' AND tbl_name='messages'").fetchall()
        db.commit()
        schema.initialize_database_schema(db)
        schema.initialize_database_schema(db)
        assert (
            db.execute(
                "SELECT rowid,chat_id,session_id,role,content,telegram_message_id,"
                "telegram_message_ids,created_at FROM messages"
            ).fetchall()
            == before
        )
        assert db.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='index' AND tbl_name='messages'"
        ).fetchall() == [
            *indexes,
            (
                "messages_narrative_cursor_idx",
                "CREATE INDEX messages_narrative_cursor_idx ON messages(chat_id,session_id,id)",
            ),
        ]
        assert db.execute("SELECT user_rowid FROM response_variants").fetchone() == (91,)
        db.execute("DELETE FROM messages")
        cursor = db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('c','s','user','later',2)"
        )
        assert cursor.lastrowid > 91
    finally:
        db.close()


@pytest.mark.parametrize("preview,fail_at", [(None, 2), (71, 2), (71, 3)])
def test_acknowledged_chunks_survive_failure_and_recovery_without_duplicates(monkeypatch, preview, fail_at):
    db = ns.db()
    try:
        rowid = ns.turn(db, "assistant", "A" * 8100, 1)
        db.commit()
        acknowledged = []
        calls = []

        def request(_token, method, payload):
            calls.append((method, payload))
            if method == "sendMessage" and len(acknowledged) + 1 == fail_at:
                raise URLError("transient network failure")
            message_id = 71 if method == "editMessageText" else 80 + len(acknowledged)
            acknowledged.append(message_id)
            return {"message_id": message_id}

        monkeypatch.setattr(telegram, "telegram_request", request)
        monkeypatch.setattr(response_delivery, "telegram_request", request)
        settings = SettingsBuilder().build()
        with pytest.raises(response_delivery.DeliveryFailure):
            response_delivery.send_reply(
                "token", "chat", "A" * 8100, db, "s1", rowid, replace_message_id=preview, app_settings=settings
            )
        assert (
            json.loads(db.execute("SELECT telegram_message_ids FROM messages WHERE rowid=?", (rowid,)).fetchone()[0])
            == acknowledged
        )
        assert acknowledged
        from bridge.delivery_progress import delivery_complete

        assert not delivery_complete(db, rowid)
        old_count = len(acknowledged)
        monkeypatch.setattr(
            telegram, "telegram_request", lambda _t, m, p: calls.append((m, p)) or {"message_id": 90 + len(calls)}
        )
        monkeypatch.setattr(
            response_delivery,
            "telegram_request",
            lambda _t, m, p: calls.append((m, p)) or {"message_id": 90 + len(calls)},
        )
        response_delivery.send_reply(
            "token", "chat", "A" * 8100, db, "s1", rowid, replace_message_id=preview, app_settings=settings
        )
        assert delivery_complete(db, rowid)
        ids = json.loads(db.execute("SELECT telegram_message_ids FROM messages WHERE rowid=?", (rowid,)).fetchone()[0])
        assert len(ids) == 3 and ids[:old_count] == acknowledged
        assert len([c for c in calls if c[0] == "editMessageText"]) == (1 if preview else 0)
        deleted = []
        monkeypatch.setattr(response_delivery, "telegram_request", lambda _t, m, p: deleted.append(p["message_id"]))
        response_delivery.delete_outgoing_messages(db, "token", "chat", "s1")
        assert deleted == ids
    finally:
        db.close()


@pytest.mark.parametrize("kind", ["text", "photo", "image_document", "voice"])
@pytest.mark.parametrize("actor", ["100", "200"])
def test_conversational_ingress_enforces_owner_before_queue_or_choice_changes(tmp_path, monkeypatch, kind, actor):
    from test_edited_group_policy import case, edited

    fixture = case.__wrapped__(tmp_path)
    c = next(fixture)
    try:
        from bridge import update_message_routing

        monkeypatch.setattr(update_message_routing, "send_help_command", lambda *a, **k: False)
        message = edited(actor)
        if kind != "text":
            message.pop("text")
        if kind == "photo":
            message["photo"] = [{"file_id": "photo"}]
        if kind == "image_document":
            message["document"] = {"file_id": "image", "file_name": "image.jpg", "mime_type": "image/jpeg"}
        if kind == "voice":
            message["voice"] = {"file_id": "voice"}
        changes = []
        monkeypatch.setattr(update_message_routing, "invalidate_choice_sets", lambda *a: changes.append(a) or [])
        update_message_routing.route_message_update(c.services, c.db, {}, message, 90, frozenset({"100", "200"}))
        assert c.services.jobs.enqueue.call_count == (1 if actor == "100" else 0)
        if actor == "200":
            assert not changes and c.sent == ["It is not your turn in manual group mode."]
        else:
            payload = c.services.jobs.enqueue.call_args.args[-1]
            assert payload["actor_id"] == actor
            assert c.services.jobs.enqueue.call_args.args[3] == "target"
    finally:
        fixture.close()


@pytest.mark.parametrize("kind", ["message", "image", "voice", "edit", "callback", "document", "choices"])
def test_transient_claim_reaches_guard_then_business_executes_once(tmp_path, monkeypatch, kind):
    from types import SimpleNamespace

    from settings_test_support import make_test_settings

    from bridge import document_jobs, job_store, light_novel_jobs, voice_jobs, worker_orchestration
    from bridge.job_service import JobService
    from bridge.scheduler_safety import DurableWorkerGuard
    from bridge.session_naming import create_session
    from bridge.sqlite_store import db_connect

    config = make_test_settings(home=tmp_path, db_file=tmp_path / "claims.sqlite3")
    db = db_connect(app_settings=config)
    create_session(db, "chat", "model", session_id="s1", app_settings=config)
    job_id = job_store.enqueue_job(db, 1, "chat", "s1", 77, kind, {"actor_id": "100"})
    job_store.mark_job_scheduled(db, job_id)
    claims = []

    def claim(conn, jid):
        claims.append(jid)
        if len(claims) == 1:
            raise sqlite3.OperationalError("database is locked")
        return job_store.mark_job_running(conn, jid)

    jobs = JobService(
        job_store.enqueue_job,
        job_store.store_job_payload,
        job_store.job_actor_id,
        job_store.mark_job_scheduled,
        claim,
        job_store.finish_job,
        job_store.recover_jobs,
        lambda *a: True,
        delivery_retry_backend=job_store.retry_delivery_job,
    )
    executed = []

    def business(*a, **k):
        executed.append(kind)

    services = SimpleNamespace(
        config=config,
        jobs=jobs,
        db_factory=lambda: db_connect(app_settings=config),
        telegram=SimpleNamespace(send_text=lambda *a: executed.append("false failure"), request=lambda *a: {}),
        group=SimpleNamespace(user_turn_allowed=lambda *a: True),
        conversation=SimpleNamespace(process_message=business),
        session=SimpleNamespace(load=lambda *a: ns.session()),
        provider=object(),
        memory=object(),
        npc=object(),
        persona=object(),
        rag=object(),
        group_director=object(),
    )
    monkeypatch.setattr(worker_orchestration, "edit_telegram_user_message", business)
    monkeypatch.setattr(worker_orchestration, "process_callback", business)
    monkeypatch.setattr(worker_orchestration, "card_fields_from_file", lambda *a, **k: {})
    monkeypatch.setattr(worker_orchestration, "process_image_message", business)
    services.telegram.download_file = lambda *a: b"image"
    monkeypatch.setattr(voice_jobs, "process_voice_message", business)
    monkeypatch.setattr(document_jobs, "import_telegram_document", business)
    if kind == "choices":
        monkeypatch.setattr(
            light_novel_jobs,
            "load_choice_set",
            lambda *a: SimpleNamespace(
                chat_id="chat", session_id="s1", assistant_rowid=0, strategy="b", generation_status="done"
            ),
        )
        monkeypatch.setattr(light_novel_jobs, "current_choice_story", lambda *a: "story")
        monkeypatch.setattr(light_novel_jobs, "card_fields_from_file", lambda *a, **k: {})
        monkeypatch.setattr(light_novel_jobs, "ensure_choices", business)
        monkeypatch.setattr(light_novel_jobs, "render_choices", lambda *a, **k: None)
    worker, args = {
        "message": (
            worker_orchestration.process_message_job,
            (services, {}, "chat", "question", 77, None, "s1", None, job_id),
        ),
        "image": (
            worker_orchestration.process_image_job,
            (services, "chat", "f", "caption", 1, 77, "s1", None, job_id),
        ),
        "voice": (voice_jobs.process_voice_job, (services, {}, "chat", {}, 77, "s1", None, job_id)),
        "edit": (worker_orchestration.process_edit_job, (services, "chat", 77, "edited", None, job_id)),
        "callback": (worker_orchestration.process_callback_job, (services, "chat", {}, job_id)),
        "document": (document_jobs.process_document_job, (services, "chat", {}, 77, "s1", None, False, job_id)),
        "choices": (light_novel_jobs.process_light_novel_choices_job, (services, "chat", "nonce", False, job_id)),
    }[kind]
    wrapped = DurableWorkerGuard(lambda path, timeout: sqlite3.connect(path), sleep=lambda _: None).prepare(
        db, job_id, worker
    )
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            wrapped(*args)
        assert db.execute("SELECT state FROM jobs WHERE job_id=?", (job_id,)).fetchone() == ("queued",)
        assert not executed
        wrapped(*args)
        assert executed == [kind]
        assert db.execute("SELECT state,attempts FROM jobs WHERE job_id=?", (job_id,)).fetchone() == ("done", 1)
        assert db.execute("SELECT count(*) FROM failed_turns").fetchone() == (0,)
    finally:
        db.close()


@pytest.mark.parametrize("transport_error", [URLError("network"), TimeoutError("timeout")])
@pytest.mark.parametrize(
    "recovery_mode", ["auto", "manual", "stale", "wrong_actor", "other_session", "changed_payload", "deleted_auto"]
)
def test_native_edit_committed_transport_failure_recovers_same_operation(
    tmp_path, monkeypatch, transport_error, recovery_mode
):
    from types import SimpleNamespace

    from application_test_setup import make_test_delivery_port, make_test_session_service
    from settings_test_support import make_test_settings

    from bridge import group_core, job_store, worker_orchestration
    from bridge.delivery_progress import delivery_complete
    from bridge.job_service import JobService
    from bridge.operations import operation_phase
    from bridge.session_naming import create_session
    from bridge.sqlite_store import db_connect

    config = make_test_settings(home=tmp_path, db_file=tmp_path / "native.sqlite3")
    db = db_connect(app_settings=config)
    create_session(db, "chat", "model", session_id="s1", app_settings=config)
    db.execute(
        "INSERT INTO messages(chat_id,session_id,role,content,telegram_message_id,created_at) "
        "VALUES('chat','s1','user','original','77',1)"
    )
    db.execute(
        "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('chat','s1','assistant','old reply',2)"
    )
    db.commit()
    job_id = job_store.enqueue_job(db, 1, "chat", "s1", 77, "edit", {"text": "edited", "actor_id": "100"})
    calls = []
    provider = make_test_provider_port(generate_backend=lambda *a, **k: calls.append("provider") or "A" * 8100)
    jobs = JobService(
        job_store.enqueue_job,
        job_store.store_job_payload,
        job_store.job_actor_id,
        job_store.mark_job_scheduled,
        job_store.mark_job_running,
        job_store.finish_job,
        job_store.recover_jobs,
        lambda *a: True,
        delivery_retry_backend=job_store.retry_delivery_job,
    )
    notices = []
    services = SimpleNamespace(
        config=config,
        jobs=jobs,
        db_factory=lambda: db_connect(app_settings=config),
        provider=provider,
        memory=make_test_memory_service(),
        npc=NpcService(),
        persona=make_test_persona_service(),
        rag=make_test_rag_service(),
        telegram=SimpleNamespace(send_text=lambda *a: notices.append(a[-1])),
        group=SimpleNamespace(user_turn_allowed=group_core.group_user_turn_allowed),
        session=make_test_session_service(app_settings=config),
    )
    monkeypatch.setattr(edit_messages, "card_fields_from_file", lambda *a, **k: ns.fields())
    monkeypatch.setattr(edit_messages, "send_typing", lambda *a: None)
    delivered = []
    transport_calls = []
    offline = [True]

    def request(_t, method, payload):
        assert not db.in_transaction
        if method == "sendMessage":
            transport_calls.append(payload["text"])
            if offline[0] and (len(transport_calls) == 2 or (recovery_mode != "auto" and len(transport_calls) > 2)):
                raise transport_error
            mid = 80 + len(delivered)
            delivered.append(mid)
            return {"message_id": mid}
        return {}

    monkeypatch.setattr(telegram, "telegram_request", request)
    monkeypatch.setattr(response_delivery, "telegram_request", request)
    worker_orchestration.process_edit_job(services, "chat", 77, "edited", job_id=job_id)
    try:
        assert operation_phase(db, job_id) == "local_committed"
        assert db.execute("SELECT state,attempts FROM jobs WHERE job_id=?", (job_id,)).fetchone() == ("queued", 1)
        assert db.execute("SELECT content FROM messages WHERE role='user'").fetchone() == ("edited",)
        rowid = db.execute("SELECT rowid FROM messages WHERE role='assistant'").fetchone()[0]
        assert not delivery_complete(db, rowid)
        if recovery_mode == "deleted_auto":
            db.execute("DELETE FROM messages")
            db.commit()
            monkeypatch.setattr(edit_messages, "send_text", lambda *a: notices.append(a[-1]))
            worker_orchestration.process_edit_job(services, "chat", 77, "edited", job_id=job_id)
            assert db.execute("SELECT state FROM jobs WHERE job_id=?", (job_id,)).fetchone() == ("failed",)
            assert operation_phase(db, job_id) == "local_committed" and calls == ["provider"]
            assert "deleted or replaced" in notices[-1]
            return
        # The owner changed after commit: recovery remains delivery only.
        group_core.save_group_state(
            db,
            "chat",
            "s1",
            {
                "enabled": True,
                "mode": "manual",
                "turn_user_id": "200",
                "turn_users": ["100", "200"],
                "members": ["a", "b"],
            },
        )
        if recovery_mode == "auto":
            worker_orchestration.process_edit_job(services, "chat", 77, "edited", job_id=job_id)
        else:
            worker_orchestration.process_edit_job(services, "chat", 77, "edited", job_id=job_id)
            worker_orchestration.process_edit_job(services, "chat", 77, "edited", job_id=job_id)
            assert db.execute("SELECT state,attempts FROM jobs WHERE job_id=?", (job_id,)).fetchone() == ("failed", 3)
            assert len(notices) == 1 and "saved" in notices[0] and "/retry" in notices[0]
            offline[0] = False
            if recovery_mode == "stale":
                db.execute("DELETE FROM messages WHERE rowid=?", (rowid,))
                db.commit()
            if recovery_mode == "changed_payload":
                db.execute(
                    "UPDATE assistant_delivery_progress SET payload='unrelated' WHERE assistant_rowid=?", (rowid,)
                )
                db.commit()
            from bridge import command_routes
            from bridge.request_types import RequestContext

            monkeypatch.setattr(command_routes, "send_text", lambda *a: notices.append(a[-1]))
            retry_session = dict(ns.session(), session_id="other" if recovery_mode == "other_session" else "s1")
            handled = command_routes._handle_basic(
                db,
                "t",
                "key",
                "model",
                ns.fields(),
                "chat",
                "/retry",
                "/retry",
                retry_session,
                retry_session["session_id"],
                "model",
                "",
                "User",
                991,
                request_context=RequestContext(
                    db,
                    retry_session["session_id"],
                    "200" if recovery_mode == "wrong_actor" else "100",
                    app_settings=config,
                ),
                conversation_service=SimpleNamespace(process_message=lambda *a, **k: pytest.fail("provider retry")),
                delivery_port=make_test_delivery_port(send_text=lambda *a: notices.append(a[-1])),
                group_service=services.group,
                memory_service=services.memory,
                provider_port=provider,
            )
            assert handled
            if recovery_mode in {"stale", "wrong_actor", "other_session", "changed_payload"}:
                assert operation_phase(db, job_id) == "local_committed"
                assert len(delivered) == 1 and calls == ["provider"]
                return
        assert operation_phase(db, job_id) == "applied"
        assert delivery_complete(db, rowid)
        assert db.execute("SELECT state,attempts FROM jobs WHERE job_id=?", (job_id,)).fetchone() == (
            "done",
            2 if recovery_mode == "auto" else 3,
        )
        assert calls == ["provider"]
        expected_chunks = telegram.split_telegram_text("✏️ Edited message regenerated.\n\n" + "A" * 8100)
        assert len(delivered) == len(expected_chunks) and len(transport_calls) == len(expected_chunks) + (
            1 if recovery_mode == "auto" else 3
        )
        assert transport_calls[0].startswith("✏️ Edited message regenerated.")
        assert notices == [] if recovery_mode == "auto" else len(notices) == 1
    finally:
        db.close()


def test_changed_continuation_payload_discards_prior_delivery_checkpoint(monkeypatch):
    from application_test_setup import make_test_delivery_port

    from bridge.delivery_progress import delivery_complete
    from bridge.generation_recovery import _generation_operation_recovery

    db = ns.db()
    rowid = ns.turn(db, "assistant", "old reply", 1)
    db.commit()
    deleted = []
    monkeypatch.setattr(telegram, "telegram_request", lambda *a: {"message_id": 71})
    response_delivery.send_reply("t", "chat", "old reply", db, "s1", rowid, app_settings=SettingsBuilder().build())
    recovery = _generation_operation_recovery(
        make_test_delivery_port(
            request=lambda _t, m, p: deleted.append(p["message_id"]),
            delete_outgoing_message_row=lambda *a: response_delivery.delete_outgoing_message_row(*a),
        )
    )
    recovery.set_payload(db, 55, {"old_message_ids": ["71"]})
    db.execute("UPDATE messages SET content=? WHERE rowid=?", ("combined reply", rowid))
    db.commit()
    monkeypatch.setattr(response_delivery, "telegram_request", lambda _t, m, p: deleted.append(p["message_id"]))
    try:
        assert not delivery_complete(db, rowid)
        recovery.prepare_delivery(db, "t", "chat", rowid, 55)
        assert 71 in deleted
        monkeypatch.setattr(telegram, "telegram_request", lambda *a: {"message_id": 72})
        response_delivery.send_reply(
            "t",
            "chat",
            "↪️ Continued response\n\ncombined reply",
            db,
            "s1",
            rowid,
            app_settings=SettingsBuilder().build(),
        )
        assert delivery_complete(db, rowid)
        assert json.loads(
            db.execute("SELECT telegram_message_ids FROM messages WHERE rowid=?", (rowid,)).fetchone()[0]
        ) == [72]
    finally:
        db.close()


def test_checkpoint_write_failure_never_marks_delivery_complete(monkeypatch):
    from bridge.delivery_progress import DeliveryFailure, delivery_complete

    db = ns.db()
    rowid = ns.turn(db, "assistant", "answer", 1)
    db.commit()
    monkeypatch.setattr(telegram, "telegram_request", lambda *a: {"message_id": 71})
    # SQLite rejects the checkpoint, representing an actual persistence failure.
    db.execute(
        "CREATE TRIGGER reject_checkpoint BEFORE UPDATE ON assistant_delivery_progress "
        "BEGIN SELECT RAISE(ABORT,'checkpoint failure'); END"
    )
    try:
        with pytest.raises(DeliveryFailure):
            response_delivery.send_reply("t", "chat", "answer", db, "s1", rowid, app_settings=SettingsBuilder().build())
        assert not delivery_complete(db, rowid)
        assert db.execute("SELECT complete FROM assistant_delivery_progress").fetchone() == (0,)
    finally:
        db.close()


def test_delivery_retry_exhaustion_is_bounded_and_keeps_saved_reply(tmp_path, monkeypatch):
    from bridge import job_store
    from bridge.delivery_progress import DeliveryFailure

    db = ns.db()
    jid = job_store.enqueue_job(db, 1, "chat", "s1", 77, "edit", {})
    try:
        for attempt in range(1, 4):
            assert job_store.mark_job_running(db, jid)
            assert job_store.retry_delivery_job(db, jid, DeliveryFailure("offline")) is (attempt < 3)
        assert db.execute("SELECT attempts,state FROM jobs WHERE job_id=?", (jid,)).fetchone() == (3, "running")
    finally:
        db.close()


def test_message_identity_migration_failure_rolls_back_data_and_ledger():
    from bridge.migrations import Migration, MigrationError
    from bridge.transcript_schema import migrate_message_identity

    db = sqlite3.connect(":memory:")
    run_migrations(db, schema.SCHEMA_MIGRATIONS[:6])
    db.execute(
        "INSERT INTO messages(rowid,chat_id,session_id,role,content,created_at) VALUES(91,'c','s','user','preserved',1)"
    )
    db.commit()

    def broken(connection):
        migrate_message_identity(connection)
        raise sqlite3.OperationalError("fail after rebuild")

    try:
        with pytest.raises(MigrationError, match="fail after rebuild"):
            run_migrations(db, (*schema.SCHEMA_MIGRATIONS[:6], Migration(7, "broken", broken)))
        assert db.execute("SELECT rowid,content FROM messages").fetchall() == [(91, "preserved")]
        assert db.execute("SELECT max(version) FROM schema_migrations").fetchone() == (6,)
        assert "AUTOINCREMENT" not in db.execute("SELECT sql FROM sqlite_master WHERE name='messages'").fetchone()[0]
    finally:
        db.close()


def test_committed_lookup_never_borrows_next_turn_assistant():
    from bridge.transcript_repository import committed_assistant_for_message

    db = ns.db()
    ns.turn(db, "user", "failed prompt", 1)
    db.execute("UPDATE messages SET telegram_message_id='77'")
    ns.turn(db, "user", "unrelated prompt", 2)
    ns.turn(db, "assistant", "unrelated answer", 3)
    db.commit()
    try:
        assert committed_assistant_for_message(db, "chat", 77) is None
    finally:
        db.close()


@pytest.mark.parametrize("kind", ["message", "image", "voice"])
def test_queued_media_rechecks_changed_owner_before_external_work(tmp_path, monkeypatch, kind):
    from types import SimpleNamespace

    from test_edited_group_policy import CHAT, case

    from bridge import group_core, voice_jobs, worker_orchestration

    fixture = case.__wrapped__(tmp_path)
    c = next(fixture)
    try:
        c.services.jobs.actor_id.return_value = "100"
        state = group_core.group_state(c.db, CHAT, "target")
        group_core.save_group_state(c.db, CHAT, "target", dict(state, turn_user_id="200"))
        c.services.conversation = SimpleNamespace(process_message=lambda *a, **k: pytest.fail("denied generation"))
        c.services.telegram.download_file = lambda *a: pytest.fail("denied download")
        monkeypatch.setattr(voice_jobs, "download_telegram_file", lambda *a: pytest.fail("denied voice download"))
        monkeypatch.setattr(voice_jobs, "transcribe_audio_bytes", lambda *a, **k: pytest.fail("denied transcription"))
        monkeypatch.setattr(voice_jobs, "send_text", lambda *a: c.sent.append(a[-1]))
        if kind == "message":
            worker_orchestration.process_message_job(
                c.services, {}, CHAT, "new prompt", 88, queued_session_id="target", job_id=91
            )
        elif kind == "image":
            worker_orchestration.process_image_job(
                c.services, CHAT, "f", "caption", 1, 88, queued_session_id="target", job_id=91
            )
        else:
            voice_jobs.process_voice_job(
                c.services, {}, CHAT, {"file_id": "f"}, 88, queued_session_id="target", job_id=91
            )
        assert c.sent == ["It is not your turn in manual group mode."]
        c.services.jobs.fail.assert_not_called()
        c.services.jobs.complete.assert_called_once()
        assert c.db.execute("SELECT count(*) FROM messages").fetchone() == (1,)
    finally:
        fixture.close()


@pytest.mark.parametrize("mime", ["image/png", "application/octet-stream"])
@pytest.mark.parametrize("actor", ["100", "200"])
def test_png_document_policy_precedes_queue_and_download(tmp_path, monkeypatch, mime, actor):
    from test_edited_group_policy import CHAT, case, edited

    from bridge import document_jobs, update_message_routing

    fixture = case.__wrapped__(tmp_path)
    c = next(fixture)
    try:
        message = edited(actor)
        message.pop("text")
        message["document"] = {"file_id": "png", "file_name": "ambiguous.png", "mime_type": mime}
        update_message_routing.route_message_update(c.services, c.db, {}, message, 90, frozenset({"100", "200"}))
        assert c.services.jobs.enqueue.call_count == (1 if actor == "100" else 0)
        c.services.jobs.actor_id.return_value = actor
        c.services.group_director = object()
        imported = []
        monkeypatch.setattr(document_jobs, "import_telegram_document", lambda *a, **k: imported.append(k))
        document_jobs.process_document_job(c.services, CHAT, message["document"], 77, "target", job_id=91)
        assert len(imported) == (1 if actor == "100" else 0)
        if actor == "200":
            assert c.sent == ["It is not your turn in manual group mode."] * 2
            c.services.jobs.fail.assert_not_called()
    finally:
        fixture.close()


@pytest.mark.parametrize("group", [False, True])
def test_greeting_partial_delivery_resumes_original_opening_without_duplicate_rows(monkeypatch, group):
    from bridge import greetings, group_core
    from bridge.delivery_progress import delivery_complete
    from bridge.operations import operation_phase

    db = ns.db()
    if group:
        group_core.save_group_state(
            db, "chat", "s1", {"enabled": True, "mode": "manual", "turn_user_id": "100", "members": ["a", "b"]}
        )
    calls = []

    def request(_t, method, payload):
        calls.append(payload["text"])
        if len(calls) == 2:
            raise TimeoutError("network")
        return {"message_id": 80 + len(calls)}

    monkeypatch.setattr(telegram, "telegram_request", request)
    fields = dict(ns.fields(), first_mes="A" * 8100)
    try:
        with pytest.raises(response_delivery.DeliveryFailure):
            greetings.send_character_greeting(
                db, "t", "chat", fields, "s1", "User", operation_id=55, app_settings=SettingsBuilder().build()
            )
        rowid, encoded = db.execute("SELECT rowid,telegram_message_ids FROM messages").fetchone()
        assert json.loads(encoded) == [81]
        assert not delivery_complete(db, rowid)
        assert operation_phase(db, 55) == "local_committed"
        assert greetings.send_character_greeting(
            db, "t", "chat", fields, "s1", "User", operation_id=55, app_settings=SettingsBuilder().build()
        )
        assert db.execute("SELECT count(*) FROM messages").fetchone() == (1,)
        assert delivery_complete(db, rowid) and operation_phase(db, 55) == "applied"
        assert len(calls) == 4
    finally:
        db.close()


def test_missing_first_chunk_acknowledgement_stops_before_later_chunks(monkeypatch):
    db = ns.db()
    rowid = ns.turn(db, "assistant", "A" * 8100, 1)
    db.commit()
    calls = []
    monkeypatch.setattr(telegram, "telegram_request", lambda *a: calls.append(a) or {})
    try:
        with pytest.raises(response_delivery.DeliveryFailure):
            response_delivery.send_reply(
                "t", "chat", "A" * 8100, db, "s1", rowid, app_settings=SettingsBuilder().build()
            )
        assert len(calls) == 1
        assert db.execute("SELECT telegram_message_ids FROM messages").fetchone() == ("[]",)
    finally:
        db.close()


def test_queued_edit_command_and_delivery_recovery_keep_enqueued_session(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from application_test_setup import make_test_application_services
    from settings_test_support import make_test_settings

    from bridge import job_store, message_commands, update_message_routing, worker_orchestration
    from bridge.job_service import DurableJob, JobService
    from bridge.operations import operation_phase
    from bridge.session_naming import create_session
    from bridge.sqlite_store import db_connect

    config = make_test_settings(home=tmp_path, db_file=tmp_path / "commands.sqlite3")
    db = db_connect(app_settings=config)
    create_session(db, "chat", "model", session_id="target", app_settings=config)
    db.execute(
        "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('chat','target','user','original',1)"
    )
    db.execute(
        "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('chat','target','assistant','old',2)"
    )
    db.commit()
    provider_calls = []
    services = make_test_application_services(
        app_settings=config,
        provider=make_test_provider_port(
            generate_backend=lambda *a, **k: provider_calls.append("provider") or "A" * 8100
        ),
    )
    services.db_factory = lambda: db_connect(app_settings=config)
    services.telegram = SimpleNamespace(send_text=lambda *a: [901], request=lambda *a: {})
    submitted = []
    services.jobs = JobService(
        job_store.enqueue_job,
        job_store.store_job_payload,
        job_store.job_actor_id,
        job_store.mark_job_scheduled,
        job_store.mark_job_running,
        job_store.finish_job,
        job_store.recover_jobs,
        lambda *a: submitted.append(a) or True,
        delivery_retry_backend=job_store.retry_delivery_job,
    )
    monkeypatch.setattr(update_message_routing, "send_help_command", lambda *a, **k: False)
    monkeypatch.setattr(message_commands, "card_fields_from_file", lambda *a, **k: ns.fields())
    monkeypatch.setattr(edit_messages, "send_typing", lambda *a: None)
    transport_calls = []

    def request(_t, m, p):
        if m == "sendMessage":
            transport_calls.append(p["text"])
            if len(transport_calls) == 2:
                raise TimeoutError("interrupted")
            return {"message_id": 80 + len(transport_calls)}
        return {}

    monkeypatch.setattr(telegram, "telegram_request", request)
    monkeypatch.setattr(response_delivery, "telegram_request", request)
    try:
        update_message_routing.route_message_update(
            services,
            db,
            {},
            {"from": {"id": "100"}, "chat": {"id": "chat"}, "message_id": 88, "text": "/edit revised"},
            1,
            frozenset({"100"}),
        )
        _, _, worker, *args = submitted[0]
        jid = args[-1]
        assert args[-3] == "target"
        create_session(db, "chat", "model", session_id="other", app_settings=config)
        worker(*args)
        assert operation_phase(db, jid) == "local_committed"
        assert db.execute("SELECT content FROM messages WHERE session_id='target' AND role='user'").fetchone() == (
            "revised",
        )
        assert db.execute("SELECT count(*) FROM messages WHERE session_id='other'").fetchone() == (0,)
        # Even a legacy queued payload requesting active resolution is pinned on recovery.
        job = DurableJob(
            jid, "chat", "target", 88, "command", {"text": "/edit revised", "actor_id": "100", "resolve_active": True}
        )
        recovered = worker_orchestration.resolve_recovered_job_submission(services, {}, job)
        assert recovered.args[-2] == "target"
        recovered.worker(*recovered.args, jid)
        assert operation_phase(db, jid) == "applied"
        assert provider_calls == ["provider"]
        assert db.execute("SELECT count(*) FROM messages WHERE session_id='other'").fetchone() == (0,)
    finally:
        db.close()


def test_legacy_committed_delivery_rejects_target_from_another_session():
    from application_test_setup import make_test_delivery_port

    from bridge import continuation
    from bridge.generation_recovery import _generation_operation_recovery
    from bridge.operations import begin_operation, set_operation_phase

    db = ns.db()
    ns.turn(db, "user", "prompt", 1)
    ns.turn(db, "assistant", "answer", 2)
    foreign = int(
        db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) "
            "VALUES('chat','other','assistant','foreign',3)"
        ).lastrowid
    )
    db.commit()
    delivery = make_test_delivery_port(send_reply=lambda *a, **k: pytest.fail("wrong-session delivery"))
    recovery = _generation_operation_recovery(delivery)
    begin_operation(db, 55, "continue")
    set_operation_phase(db, 55, "continue", "local_committed")
    recovery.set_payload(db, 55, {"assistant_rowid": foreign})
    try:
        with pytest.raises(response_delivery.DeliveryFailure):
            continuation.continue_last(
                db,
                "t",
                "k",
                ns.session(),
                ns.fields(),
                "chat",
                55,
                provider_port=make_test_provider_port(generate_backend=lambda *a, **k: pytest.fail("generation")),
                delivery_port=delivery,
                memory_service=make_test_memory_service(),
                npc_service=NpcService(),
                persona_service=make_test_persona_service(),
                app_settings=SettingsBuilder().build(),
                rag_service=make_test_rag_service(),
            )
    finally:
        db.close()
