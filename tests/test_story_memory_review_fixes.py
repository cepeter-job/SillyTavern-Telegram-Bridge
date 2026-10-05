"""Focused Stage2 review regressions: ownership, request era, and prefix validity."""

import json
from types import SimpleNamespace

import pytest
from application_test_setup import make_test_provider_port
from memory_runtime_test_support import isolated_memory_runtime as isolated_memory_runtime
from settings_test_support import make_test_settings
from test_story_memory_artifacts import service, write_artifacts
from test_story_memory_scope import accept, append, scope
from test_story_memory_scope import db as db
from test_story_memory_scope import forbid_real_hindsight_client as forbid_real_hindsight_client

from bridge import memory_backend
from bridge.memory_artifact_store import read_scene_block, read_summary_block
from bridge.memory_fact_store import index_fact_is_current
from bridge.memory_store import claim_jobs, pending_memory_invalidation
from bridge.memory_workers import run_memory_claim
from bridge.sqlite_store import write_transaction


@pytest.fixture(autouse=True)
def isolate_native_persona_metadata(monkeypatch, caplog):
    from bridge import npc_extraction, persona_sync

    monkeypatch.setattr(npc_extraction, "persona_name", lambda *a, **k: "Synthetic User")
    monkeypatch.setattr(
        persona_sync,
        "_native_settings",
        lambda *a, **k: pytest.fail("NPC review tests must not read native Persona settings"),
    )
    yield
    assert not any("Could not load native Persona metadata" in record.getMessage() for record in caplog.records)


def run_claim(db, claim, *, app_settings=None, **kwargs):
    return run_memory_claim(
        db,
        claim,
        {"session_id": "s", "model_id": "m"},
        {"name": "Mira"},
        app_settings=app_settings or make_test_settings(),
        **kwargs,
    )


@pytest.mark.parametrize("interruption", ["rewrite", "lease", "mode"])
def test_valid_index_remains_retryable_after_interrupted_retain(db, monkeypatch, interruption):
    append(db)
    accept(db)
    later = append(db, "An unrelated later weather report.")
    document = db.execute("SELECT document_id FROM memory_fact_index").fetchone()[0]
    calls = []
    replacements = []

    def retain(*args, **kwargs):
        if args[6] == "native_fact":
            calls.append(args[2])
            if len(calls) == 1:
                if interruption == "rewrite":
                    db.execute("UPDATE messages SET content='A corrected later forecast.' WHERE id=?", (later,))
                elif interruption == "mode":
                    db.execute("INSERT INTO meta VALUES('memory_mode:c','off')")
                else:
                    db.execute("UPDATE memory_jobs SET lease_deadline=0 WHERE layer='hindsight'")
                db.commit()
                if interruption == "lease":
                    replacements.append(claim_jobs(db, layers=("hindsight",))[0])
        return True

    monkeypatch.setattr(memory_backend, "_retain_with_client", retain)
    first = claim_jobs(db, layers=("hindsight",))[0]
    result = run_claim(db, first)
    assert result == ("disabled" if interruption == "mode" else "stale_source")
    assert db.execute("SELECT state FROM memory_fact_index WHERE document_id=?", (document,)).fetchone() == ("pending",)
    assert index_fact_is_current(db, document) is not None
    assert db.execute("SELECT 1 FROM memory_retired_documents WHERE document_id=?", (document,)).fetchone() is None
    assert db.execute("SELECT 1 FROM hindsight_documents WHERE document_id=?", (document,)).fetchone() is None
    assert db.execute("SELECT completed_version FROM memory_jobs WHERE layer='hindsight'").fetchone()[0] == 0
    if replacements:
        assert (
            db.execute("SELECT lease_token FROM memory_jobs WHERE layer='hindsight'").fetchone()[0]
            == replacements[0].token
        )
        retry = replacements[0]
    else:
        db.execute("UPDATE meta SET value='on' WHERE key='memory_mode:c'")
        db.execute("UPDATE memory_jobs SET next_attempt_at=0")
        db.commit()
        retry = claim_jobs(db, layers=("hindsight",))[0]
    assert run_claim(db, retry) == "complete"
    assert calls == [document, document]
    assert db.execute("SELECT state FROM memory_fact_index WHERE document_id=?", (document,)).fetchone() == (
        "retained",
    )
    assert db.execute("SELECT count(*) FROM episodic_memories").fetchone()[0] == 1
    assert db.execute("SELECT 1 FROM memory_retired_documents WHERE document_id=?", (document,)).fetchone() is None


def test_slow_recall_excludes_reindexed_replacement_but_keeps_unchanged_prefix(db, tmp_path, monkeypatch):
    from functools import partial

    settings = make_test_settings(home=tmp_path)

    append(db, "The earlier silver key opens the harbor.")
    _, earlier_id = accept(db, "The earlier silver key opens the harbor.", (), "shared")
    changed_row = append(db, "The later silver key opens the attic.")
    accept(db, "The later silver key opens the attic.", (), "shared")
    monkeypatch.setattr(memory_backend, "_retain_with_client", lambda *a, **k: True)
    assert run_claim(db, claim_jobs(db, layers=("hindsight",))[0], app_settings=settings) == "complete"
    documents = [db.execute("SELECT document_id FROM memory_fact_index WHERE memory_id=?", (earlier_id,)).fetchone()[0]]
    calls = []

    class Client:
        def __init__(self):
            self.documents = self

        async def delete_document(self, **kwargs):
            pass

        def recall(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                db.execute(
                    "UPDATE messages SET content='The replacement silver key opens the vault.' WHERE id=?",
                    (changed_row,),
                )
                db.commit()
                _, replacement_id = accept(db, "The replacement silver key opens the vault.", (), "shared")
                assert run_claim(db, claim_jobs(db, layers=("hindsight",))[0], app_settings=settings) == "complete"
                documents.append(
                    db.execute(
                        "SELECT document_id FROM memory_fact_index WHERE memory_id=?", (replacement_id,)
                    ).fetchone()[0]
                )
            return SimpleNamespace(
                results=[SimpleNamespace(document_id=doc, text="untrusted", type="world") for doc in documents]
            )

        def close(self):
            pass

    monkeypatch.setattr(memory_backend, "hindsight_client", lambda **kwargs: Client())
    memory = service(partial(memory_backend.recall_scoped_memory, app_settings=settings))
    old = memory.prompt_context(db, "c", {"session_id": "s"}, {"name": "Mira"}, "silver key")
    assert (old.recall + old.episodic).count("harbor") == 1
    assert "vault" not in old.recall + old.episodic and "attic" not in old.recall + old.episodic
    fresh = memory.prompt_context(db, "c", {"session_id": "s"}, {"name": "Mira"}, "silver key")
    assert "vault" in fresh.recall and "harbor" in fresh.recall
    assert fresh.scope.rewrite_revision > old.scope.rewrite_revision


def test_npc_recovery_after_recall_respects_old_request_and_preserves_prefix(db, tmp_path):
    from bridge.memory_contracts import MemoryBlock
    from bridge.npc_repository import set_npc_extraction_coverage
    from bridge.npc_service import NpcService
    from bridge.npc_types import NpcExtractionGroup, NpcOperation

    npc = NpcService()
    first = append(db, "Maya has a soft voice.")
    second = append(db, "Maya feels calm.")
    for row, field, value, mode in [(first, "voice", "soft", "fixed"), (second, "mood", "calm", "mutable")]:
        assert (
            npc.apply_group(
                db,
                "c",
                "s",
                NpcExtractionGroup("Maya", (), (NpcOperation(field, "set", value, mode, "shared", ()),)),
                source_rowid=row,
                primary_name="Mira",
                user_name="User",
            ).applied
            == 1
        )
    with write_transaction(db):
        set_npc_extraction_coverage(db, "c", "s", second, 3)

    def recall(*_args):
        db.execute("UPDATE messages SET content='Maya feels replacement anxiety.' WHERE id=?", (second,))
        db.commit()

        def generate(_key, _model, messages, **kwargs):
            part = messages[-1]["content"].split("\n\nCanonical source part:\n", 1)[1]
            operations = (
                [
                    {
                        "field": "mood",
                        "op": "set",
                        "value": "replacement anxiety",
                        "mode": "mutable",
                        "visibility": "shared",
                        "known_by": [],
                    }
                ]
                if "replacement anxiety" in part
                else []
            )
            return json.dumps(
                {"npcs": [{"name": "Maya", "aliases": [], "operations": operations}] if operations else []}
            )

        provider = make_test_provider_port(generate_backend=generate)
        assert (
            run_claim(
                db,
                claim_jobs(db, layers=("npc",))[0],
                provider_port=provider,
                app_settings=make_test_settings(home=tmp_path),
            )
            == "complete"
        )
        assert pending_memory_invalidation(db, "c", "s", "npc") is None
        return MemoryBlock(channel="recall")

    context = service(recall).prompt_context(db, "c", {"session_id": "s"}, {"name": "Mira"}, "Maya")
    old = npc.context_for_prompt(db, "c", {"session_id": "s"}, {"name": "Mira"}, "Maya", [], memory_scope=context.scope)
    fresh = npc.context_for_prompt(db, "c", {"session_id": "s"}, {"name": "Mira"}, "Maya", [], memory_scope=scope(db))
    assert "soft" in old and "replacement anxiety" not in old and "calm" not in old
    assert "soft" in fresh and "replacement anxiety" in fresh


@pytest.mark.parametrize("mutation", ["update", "delete", "move"])
def test_classified_artifacts_keep_unaffected_prefix_and_reject_overlap(db, mutation):
    first = append(db, "A public tower.")
    write_artifacts(db, first)
    later = append(db, "A later scene.")
    captured = scope(db, "Bob")
    if mutation == "delete":
        db.execute("DELETE FROM messages WHERE id=?", (later,))
    elif mutation == "move":
        db.execute("UPDATE messages SET session_id='other' WHERE id=?", (later,))
    else:
        db.execute("UPDATE messages SET content='An unrelated correction.' WHERE id=?", (later,))
    db.commit()
    assert read_summary_block(db, captured).text == "Public tower"
    assert read_scene_block(db, captured).text == "Location: The tower"
    db.execute("UPDATE messages SET content='The tower never existed.' WHERE id=?", (first,))
    db.commit()
    assert read_summary_block(db, captured).text == ""
    assert read_scene_block(db, captured).text == ""


@pytest.mark.parametrize("members", [None, "mira.png", {"name": "Mira"}])
def test_injected_ensemble_state_with_unknown_members_never_authorizes_private_fact(db, monkeypatch, members):
    import bridge.memory_scope_runtime as runtime
    from bridge.memory_scope_store import read_episodic_block

    append(db)
    accept(db)
    monkeypatch.setattr(runtime, "card_fields_from_file", lambda *a, **k: {"name": "Mira"})
    resolved = runtime.resolve_session_memory_scope(
        db,
        "c",
        {"session_id": "s"},
        {"name": "Mira"},
        app_settings=make_test_settings(),
        load_group_state=lambda *a: {"enabled": True, "mode": "autonomous", "members": members},
    )
    assert read_episodic_block(db, resolved, "silver key").text == ""


def test_scope_metadata_and_rewrite_watermark_share_one_sqlite_snapshot(db):
    from bridge.narrative_repository import load_narrative_clock

    first = append(db)
    fired = []

    def concurrent_rewrite(statement):
        if not fired and "MAX(change_id)" in statement and "memory_source_rewrites" in statement:
            fired.append(True)
            db.execute("UPDATE messages SET content='Replacement before scope snapshot.' WHERE id=?", (first,))
            db.commit()

    db.set_trace_callback(concurrent_rewrite)
    try:
        captured = scope(db)
    finally:
        db.set_trace_callback(None)
    assert fired
    current = load_narrative_clock(db, "c", "s")
    assert current["rewrite_revision"] > 0
    assert captured.rewrite_revision == current["rewrite_revision"]
    assert (
        captured.rewrite_event_cutoff == db.execute("SELECT MAX(change_id) FROM memory_source_rewrites").fetchone()[0]
    )


def test_npc_history_and_journal_bound_share_one_local_snapshot(db, tmp_path, monkeypatch):
    import sqlite3

    import bridge.npc_service as npc_module
    from bridge.npc_repository import set_npc_extraction_coverage
    from bridge.npc_service import NpcService
    from bridge.npc_types import NpcExtractionGroup, NpcOperation

    npc = NpcService()
    first = append(db, "Maya has a soft voice.")
    second = append(db, "Maya feels calm.")
    for row, field, value, mode in [(first, "voice", "soft", "fixed"), (second, "mood", "calm", "mutable")]:
        npc.apply_group(
            db,
            "c",
            "s",
            NpcExtractionGroup("Maya", (), (NpcOperation(field, "set", value, mode, "shared", ()),)),
            source_rowid=row,
            primary_name="Mira",
            user_name="User",
        )
    with write_transaction(db):
        set_npc_extraction_coverage(db, "c", "s", second, 3)
    reader = sqlite3.connect(tmp_path / "npc-snapshot.sqlite")
    db.backup(reader)
    reader.execute("PRAGMA journal_mode=WAL")
    writer = sqlite3.connect(tmp_path / "npc-snapshot.sqlite", timeout=0)
    original = npc_module.load_npc_fields_as_of
    attempts = []

    def replace_and_recover():
        writer.execute("UPDATE messages SET content='Maya feels replacement anxiety.' WHERE id=?", (second,))
        writer.commit()
        provider = make_test_provider_port(
            generate_backend=lambda *a, **k: json.dumps(
                {
                    "npcs": [
                        {
                            "name": "Maya",
                            "aliases": [],
                            "operations": [
                                {
                                    "field": "mood",
                                    "op": "set",
                                    "value": "replacement anxiety",
                                    "mode": "mutable",
                                    "visibility": "shared",
                                    "known_by": [],
                                }
                            ],
                        }
                    ]
                }
            )
        )
        assert (
            run_claim(
                writer,
                claim_jobs(writer, layers=("npc",))[0],
                provider_port=provider,
                app_settings=make_test_settings(home=tmp_path),
            )
            == "complete"
        )
        assert pending_memory_invalidation(writer, "c", "s", "npc") is None

    def racing_history_read(connection, *args):
        if not attempts:
            attempts.append(True)
            try:
                replace_and_recover()
            except sqlite3.OperationalError as exc:
                assert "locked" in str(exc)
                writer.rollback()
                attempts.append("serialized")
        return original(connection, *args)

    try:
        captured = scope(reader)
        monkeypatch.setattr(npc_module, "load_npc_fields_as_of", racing_history_read)
        result = npc.context_for_prompt(
            reader,
            "c",
            {"session_id": "s"},
            {"name": "Mira"},
            "Maya",
            [],
            memory_scope=captured,
        )
        assert attempts
        assert "soft" in result and "replacement anxiety" not in result
        assert not reader.in_transaction
        if "serialized" in attempts:
            replace_and_recover()
        fresh = npc.context_for_prompt(
            reader,
            "c",
            {"session_id": "s"},
            {"name": "Mira"},
            "Maya",
            [],
            memory_scope=scope(reader),
        )
        assert "replacement anxiety" in fresh
    finally:
        writer.close()
        reader.close()


def test_rewrite_journal_fences_both_moved_owners_and_isolates_recreated_session(db):
    from bridge.memory_scope_store import resolve_memory_scope, scope_is_current
    from bridge.memory_store import request_source_cutoff

    first = append(db, "Earlier origin source.")
    moved = append(db, "This source changes owner.")
    other_first = append(db, "Existing destination source.", sid="other")
    origin = scope(db)
    destination = resolve_memory_scope(db, "c", {"session_id": "other"}, {"name": "Mira"})
    db.execute("UPDATE messages SET session_id='other' WHERE id=?", (moved,))
    db.commit()
    assert request_source_cutoff(db, origin) == first
    assert request_source_cutoff(db, destination) == moved - 1 < other_first
    owners = {
        (row[0], row[1]) for row in db.execute("SELECT session_id,session_created_at FROM memory_source_rewrites")
    }
    assert owners == {("s", 1.0), ("other", 2.0)}
    db.execute("DELETE FROM sessions WHERE session_id='other'")
    db.execute(
        "INSERT INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,"
        "world_file,created_at,updated_at) VALUES('c','other','Recreated','','m','','',3,3)"
    )
    db.commit()
    assert not scope_is_current(db, destination)
    assert not db.execute("SELECT 1 FROM memory_source_rewrites WHERE session_id='other'").fetchone()
    fresh = resolve_memory_scope(db, "c", {"session_id": "other"}, {"name": "Mira"})
    assert fresh.session_created_at == 3 and fresh.rewrite_event_cutoff == 0
