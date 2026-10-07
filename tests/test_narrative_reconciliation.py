"""Committed-boundary reconciliation, stale-result rejection, and bounded scheduling."""

import json
from dataclasses import asdict

import pytest
from application_test_setup import make_test_provider_port
from test_memory_completion_safety import session_db as session_db

from bridge.narrative_repository import load_narrative_scene, load_narrative_thread
from bridge.narrative_settings import preset_narrative_settings, save_session_narrative_settings
from bridge.sqlite_store import write_transaction


def add_story(db, content="Mara waits at the gate, far from the user."):
    with write_transaction(db):
        cursor = db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", "s1", "assistant", content, 1.0),
        )
        return int(cursor.lastrowid)


def proposal(
    scene="gate", thread="rebellion", *, phase="development", summary="The rebellion waits.", transition="continue"
):
    return json.dumps(
        {
            "schema_version": 1,
            "story_phase": phase,
            "scene": {
                "scene_id": scene,
                "thread_id": thread,
                "viewpoint_character": "Mara",
                "pov_mode": "third_person_rotating",
                "user_present": False,
                "purpose": "Watch the gate",
                "transition_type": transition,
            },
            "threads": [{"thread_id": thread, "title": "Rebellion", "status": "active", "summary": summary}],
        }
    )


def run_reconciliation(session_db, generate=None, **kwargs):
    from bridge.narrative_reconciliation import reconcile_narrative_state_now

    settings, db, session = session_db
    return reconcile_narrative_state_now(
        db,
        "",
        "chat",
        session,
        provider_port=make_test_provider_port(generate_backend=generate or (lambda *a, **k: proposal())),
        app_settings=settings,
        **kwargs,
    )


def test_history_migration_tracks_content_not_delivery_updates(session_db):
    _settings, db, _session = session_db
    assert db.execute("SELECT name FROM schema_migrations WHERE version=11").fetchone() == (
        "narrative_history_revisions",
    )
    rowid = add_story(db)
    clock = db.execute("SELECT state_revision,history_revision FROM narrative_state").fetchone()
    with write_transaction(db):
        db.execute("UPDATE messages SET telegram_message_ids='[42]' WHERE id=?", (rowid,))
    assert db.execute("SELECT state_revision,history_revision FROM narrative_state").fetchone() == clock
    with write_transaction(db):
        db.execute("UPDATE messages SET content='Mara left the gate.' WHERE id=?", (rowid,))
    changed = db.execute(
        "SELECT state_revision,history_revision,invalidated_from_rowid FROM narrative_state"
    ).fetchone()
    assert changed == (clock[0] + 1, clock[1] + 1, rowid)


def test_reconciliation_records_exact_committed_boundary_and_keeps_physical_state(session_db):
    _settings, db, _session = session_db
    rowid = add_story(db)
    with write_transaction(db):
        db.execute("INSERT INTO scene_states VALUES(?,?,?,?,?)", ("chat", "s1", '{"weather":"rain"}', rowid, 2.0))
    before = db.execute("SELECT * FROM scene_states").fetchall()
    usage = []

    def generate(*args, **kwargs):
        assert not db.in_transaction
        assert args[1] == "dummy::model"
        assert "rain" in args[2][-1]["content"]
        usage.append(kwargs["session_id"])
        return proposal()

    state = run_reconciliation(session_db, generate)
    assert state.active_scene_id == "gate"
    assert state.active_thread_id == "rebellion"
    assert state.updated_through_rowid == rowid
    assert state.user_present is False
    assert load_narrative_thread(db, "chat", "s1", "rebellion")["summary"] == "The rebellion waits."
    assert db.execute("SELECT * FROM scene_states").fetchall() == before
    assert len(usage) == 1
    assert db.execute("SELECT COUNT(*) FROM narrative_checkpoints WHERE kind='reconciliation'").fetchone()[0] == 1


def test_current_state_needs_no_repeat_provider_call(session_db):
    _settings, db, _session = session_db
    add_story(db)
    first = run_reconciliation(session_db)
    second = run_reconciliation(session_db, lambda *a, **k: pytest.fail("already-current state generated again"))
    assert asdict(second) == asdict(first)


@pytest.mark.parametrize("change", ["append", "edit", "delete", "style", "recreate"])
def test_completion_after_revision_change_cannot_overwrite_state(session_db, change):
    settings, db, session = session_db
    first_row = add_story(db)
    run_reconciliation(session_db)
    add_story(db, "Mara considers a new route.")

    def mutate(*args, **kwargs):
        assert not db.in_transaction
        if change == "style":
            save_session_narrative_settings(db, "chat", "s1", preset_narrative_settings("observer"))
        elif change == "append":
            add_story(db, "Newer facts are already committed.")
        elif change == "recreate":
            from bridge.session_core import create_session

            with write_transaction(db):
                db.execute("DELETE FROM messages WHERE chat_id='chat' AND session_id='s1'")
                db.execute("DELETE FROM sessions WHERE chat_id='chat' AND session_id='s1'")
            create_session(db, "chat", "dummy::model", session_id="s1", app_settings=settings)
            add_story(db, "This is a different story.")
        else:
            with write_transaction(db):
                if change == "edit":
                    db.execute("UPDATE messages SET content='The gate never existed.' WHERE id=?", (first_row,))
                else:
                    db.execute("DELETE FROM messages WHERE id=?", (first_row,))
        return proposal("obsolete-scene", summary="OBSOLETE", transition="cut")

    run_reconciliation(session_db, mutate)
    assert load_narrative_scene(db, "chat", "s1", "obsolete-scene") is None
    thread = load_narrative_thread(db, "chat", "s1", "rebellion")
    assert thread is None or thread["summary"] != "OBSOLETE"
    assert not db.in_transaction
    assert session["session_id"] == "s1"


def test_edit_rebuilds_from_checkpoint_without_copying_future_state(session_db):
    _settings, db, _session = session_db
    old = add_story(db, "Mara is at the gate.")
    run_reconciliation(session_db)
    new = add_story(db, "Mara travels to a tower.")
    run_reconciliation(session_db, lambda *a, **k: proposal("tower", summary="Future tower fact", transition="cut"))
    with write_transaction(db):
        db.execute("UPDATE messages SET content='Mara instead stays at the gate.' WHERE id=?", (new,))
    seen = []

    def generate(*args, **kwargs):
        seen.append(args[2][-1]["content"])
        return proposal(summary="Mara stayed at the gate.")

    state = run_reconciliation(session_db, generate)
    assert state.updated_through_rowid == new
    assert load_narrative_scene(db, "chat", "s1", "tower") is None
    assert load_narrative_scene(db, "chat", "s1", "gate")["start_rowid"] == old
    assert all("Future tower fact" not in text for text in seen)
    assert db.execute("SELECT invalidated_from_rowid FROM narrative_state").fetchone()[0] is None


def test_reconciliation_never_calls_provider_inside_caller_transaction(session_db):
    _settings, db, _session = session_db
    add_story(db)
    db.execute("BEGIN")
    try:
        with pytest.raises(RuntimeError, match="transaction"):
            run_reconciliation(session_db, lambda *a, **k: pytest.fail("provider called in transaction"))
        assert db.in_transaction
    finally:
        db.rollback()


@pytest.mark.parametrize(
    "raw", ["not JSON", "[]", '{"schema_version":2}', "{" + "x" * 25000], ids=["text", "array", "version", "oversized"]
)
def test_invalid_output_leaves_transcript_and_existing_state_untouched(session_db, raw):
    _settings, db, _session = session_db
    add_story(db)
    run_reconciliation(session_db)
    add_story(db)
    before = db.execute("SELECT * FROM messages").fetchall()
    thread = load_narrative_thread(db, "chat", "s1", "rebellion")
    run_reconciliation(session_db, lambda *a, **k: raw)
    assert db.execute("SELECT * FROM messages").fetchall() == before
    assert load_narrative_thread(db, "chat", "s1", "rebellion") == thread


def test_stale_state_is_not_exposed_as_current_context(session_db):
    from bridge.narrative_context import narrative_context_for_session

    _settings, db, _session = session_db
    save_session_narrative_settings(db, "chat", "s1", preset_narrative_settings("observer"))
    row = add_story(db)
    run_reconciliation(session_db)
    assert '"gate"' in narrative_context_for_session(db, "chat", "s1", "story")
    with write_transaction(db):
        db.execute("UPDATE messages SET content='A different location.' WHERE id=?", (row,))
    context = narrative_context_for_session(db, "chat", "s1", "story")
    assert "cinematic/objective" in context
    assert '"gate"' not in context


def test_bounded_batches_cover_only_whole_processed_messages(session_db):
    _settings, db, _session = session_db
    for i in range(40):
        add_story(db, f"TURN_{i:02d}: " + "x" * 2000 + f" END_{i:02d}")
    prompts = []

    def generate(*args, **kwargs):
        prompts.append(args[2][-1]["content"])
        return proposal()

    state = run_reconciliation(session_db, generate)
    assert 0 < state.updated_through_rowid < 40
    assert f"END_{state.updated_through_rowid - 1:02d}" in prompts[0]
    assert len(prompts[0]) < 32000


def test_reconciliation_failure_never_marks_oversized_source_as_covered(session_db):
    _settings, db, _session = session_db
    add_story(db, "x" * 80000)
    state = run_reconciliation(session_db, lambda *a, **k: pytest.fail("oversized source was truncated"))
    assert state.updated_through_rowid == 0


def test_background_admission_coalesces_duplicate_session_requests(session_db, monkeypatch):
    from bridge import narrative_reconciliation as owner

    settings, db, session = session_db
    add_story(db)
    scheduled = []
    monkeypatch.setattr(owner, "submit_background", lambda *a, **k: scheduled.append((a, k)) or True)
    port = make_test_provider_port(generate_backend=lambda *a, **k: proposal())
    assert owner.queue_narrative_reconciliation(db, "chat", session, provider_port=port, app_settings=settings)
    assert not owner.queue_narrative_reconciliation(db, "chat", session, provider_port=port, app_settings=settings)
    assert len(scheduled) == 1
    args, kwargs = scheduled.pop()
    args[1](*args[2:], **kwargs)
    assert not owner.queue_narrative_reconciliation(db, "chat", session, provider_port=port, app_settings=settings)


def test_rejected_background_submission_releases_session_admission(session_db, monkeypatch):
    from bridge import narrative_reconciliation as owner

    settings, db, session = session_db
    add_story(db)
    monkeypatch.setattr(owner, "submit_background", lambda *a, **k: False)
    port = make_test_provider_port()
    assert not owner.queue_narrative_reconciliation(db, "chat", session, provider_port=port, app_settings=settings)
    assert not owner.queue_narrative_reconciliation(db, "chat", session, provider_port=port, app_settings=settings)


def test_rolling_reconciliation_history_is_bounded(session_db):
    _settings, db, _session = session_db
    for i in range(40):
        add_story(db, f"Mara waits, moment {i}.")
        run_reconciliation(session_db)
    assert db.execute("SELECT COUNT(*) FROM narrative_checkpoints WHERE kind='reconciliation'").fetchone()[0] == 32


def test_partial_batch_does_not_receive_future_physical_scene(session_db):
    _settings, db, _session = session_db
    for number in range(40):
        last = add_story(db, f"Moment {number}: " + "x" * 2000)
    with write_transaction(db):
        db.execute(
            "INSERT INTO scene_states VALUES(?,?,?,?,?)", ("chat", "s1", '{"location":"FUTURE_TOWER"}', last, 2.0)
        )
    prompts = []
    state = run_reconciliation(session_db, lambda *a, **k: prompts.append(a[2][-1]["content"]) or proposal())
    assert state.updated_through_rowid < last
    assert "FUTURE_TOWER" not in prompts[0]


def test_committed_scene_can_finish_its_thread_without_becoming_a_plan(session_db):
    _settings, db, _session = session_db
    add_story(db, "The gate is opened. The rebellion achieves its final aim.")
    data = json.loads(proposal(phase="resolution"))
    data["threads"][0]["status"] = "resolved"
    state = run_reconciliation(session_db, lambda *a, **k: json.dumps(data))
    assert state.story_phase == "resolution"
    assert load_narrative_thread(db, "chat", "s1", "rebellion")["status"] == "resolved"


def test_insert_inside_reconciled_prefix_invalidates_from_inserted_identity(session_db):
    _settings, db, _session = session_db
    add_story(db)
    with write_transaction(db):
        db.execute(
            "INSERT INTO messages(id,chat_id,session_id,role,content,created_at) "
            "VALUES(100,'chat','s1','assistant','Mara reached the gate.',100)"
        )
    run_reconciliation(session_db)
    with write_transaction(db):
        db.execute(
            "INSERT INTO messages(id,chat_id,session_id,role,content,created_at) "
            "VALUES(50,'chat','s1','assistant','An earlier missing event.',50)"
        )
    assert db.execute("SELECT invalidated_from_rowid FROM narrative_state").fetchone()[0] == 50


def test_late_physical_scene_refresh_cannot_recreate_reset_story_facts(session_db, monkeypatch):
    from types import SimpleNamespace

    from bridge import message_commands
    from bridge.scene_state import refresh_scene_state_now

    settings, db, session = session_db
    rowid = add_story(db, "Mara waits at an old gate.")
    monkeypatch.setattr(message_commands, "delete_tracked_panel_messages", lambda *a, **k: None)

    def generate(*args, **kwargs):
        message_commands.reset_session(
            db,
            "token",
            "chat",
            session,
            memory_service=SimpleNamespace(queue_cleanup=lambda *a: 0),
            npc_service=SimpleNamespace(purge_session=lambda *a: 0),
        )
        return '{"location":"OBSOLETE_GATE","time":"night","environment":[],"characters":{},"objects":{}}'

    refresh_scene_state_now(
        db,
        "",
        "chat",
        session,
        "Mara",
        through_rowid=rowid,
        provider_port=make_test_provider_port(generate_backend=generate),
        app_settings=settings,
    )
    assert db.execute("SELECT * FROM scene_states").fetchall() == []
    assert db.execute("SELECT * FROM narrative_state").fetchall() == []
    assert db.execute("SELECT * FROM messages").fetchall() == []


def test_reset_clears_physical_state_without_optional_extension_hooks(session_db, monkeypatch):
    from types import SimpleNamespace

    from bridge import memory, message_commands

    _settings, db, session = session_db
    rowid = add_story(db)
    with write_transaction(db):
        db.execute("INSERT INTO scene_states VALUES(?,?,?,?,?)", ("chat", "s1", '{"location":"Old gate"}', rowid, 1))
    monkeypatch.setattr(memory, "_run_summary_clear_hooks", lambda *a: None)
    monkeypatch.setattr(message_commands, "delete_tracked_panel_messages", lambda *a, **k: None)
    message_commands.reset_session(
        db,
        "token",
        "chat",
        session,
        memory_service=SimpleNamespace(queue_cleanup=lambda *a: 0),
        npc_service=SimpleNamespace(purge_session=lambda *a: 0),
    )
    assert db.execute("SELECT * FROM scene_states").fetchall() == []


def test_physical_scene_refresh_refuses_provider_work_inside_a_transaction(session_db):
    from bridge.scene_state import refresh_scene_state_now

    settings, db, session = session_db
    add_story(db)
    calls = []
    db.execute("BEGIN")
    try:
        with pytest.raises(RuntimeError, match="transaction"):
            refresh_scene_state_now(
                db,
                "",
                "chat",
                session,
                "Mara",
                app_settings=settings,
                provider_port=make_test_provider_port(
                    generate_backend=lambda *a, **k: calls.append(True) or '{"location":"Gate"}'
                ),
            )
        assert not calls
        assert db.in_transaction
    finally:
        db.rollback()
