"""Classification is written with derived payloads and survives only its exact source."""

import json
import sqlite3

import pytest
from application_test_setup import make_test_persona_service, make_test_provider_port
from memory_runtime_test_support import isolated_memory_runtime as isolated_memory_runtime
from settings_test_support import make_test_settings
from test_story_memory_scope import append, scope
from test_story_memory_scope import db as db
from test_story_memory_scope import forbid_real_hindsight_client as forbid_real_hindsight_client

from bridge import memory, scene_state
from bridge.memory_contracts import MemoryBlock
from bridge.memory_store import next_source_segment, store_segment
from bridge.migrations import run_migrations
from bridge.schema import SCHEMA_MIGRATIONS
from bridge.sqlite_store import write_transaction


def classified(text, audience=()):
    return {"text": text, "visibility": "restricted" if audience else "shared", "known_by": list(audience)}


def service(recall=None):
    from bridge.memory_artifact_store import read_scene_block, read_summary_block
    from bridge.memory_scope_store import read_episodic_block, resolve_memory_scope, validate_memory_blocks
    from bridge.memory_service import MemoryService

    return MemoryService(
        resolve_scope=resolve_memory_scope,
        scoped_recall=recall or (lambda *a: MemoryBlock(channel="recall")),
        scoped_episodes=read_episodic_block,
        scoped_summary=read_summary_block,
        scoped_scene=read_scene_block,
        validate_blocks=validate_memory_blocks,
        summary_state=memory.get_session_summary,
        retain_session=lambda *a: None,
        purge_session_memory=lambda *a: 0,
    )


def write_artifacts(db, through, *, summary="Public tower", scene="The tower", audience=()):
    from bridge.memory_artifact_store import store_artifact_visibility

    with write_transaction(db):
        db.execute("INSERT OR REPLACE INTO session_summaries VALUES('c','s',?,?,1)", (summary, through))
        state_json = json.dumps({"location": scene, "facts": ["PRIVATE PASSCODE"]}, sort_keys=True)
        db.execute("INSERT OR REPLACE INTO scene_states VALUES('c','s',?,?,1)", (state_json, through))
        store_artifact_visibility(db, "c", "s", "summary", [classified(summary, audience)])
        store_artifact_visibility(
            db,
            "c",
            "s",
            "scene",
            [
                classified("Location: " + scene),
                classified("PRIVATE PASSCODE", ("Mira",)),
            ],
        )


def test_classified_public_scene_and_summary_reach_real_prompt_assembly(db):
    from bridge.generation import build_chat_messages

    row = append(db, "At the public tower, Mira knows a private passcode.")
    write_artifacts(db, row)
    context = service().prompt_context(db, "c", {"session_id": "s"}, {"name": "Bob"}, "tower")
    assert context.summary == "Public tower"
    assert context.scene == "Location: The tower"
    assert "PRIVATE" not in context.scene
    fields = dict.fromkeys(
        (
            "system_prompt",
            "description",
            "personality",
            "scenario",
            "first_mes",
            "mes_example",
            "post_history_instructions",
        ),
        "",
    )
    fields["name"] = "Bob"
    messages = build_chat_messages(
        {"session_id": "s", "persona_id": "", "world_file": ""},
        fields,
        "Look around.",
        [],
        scene_context=context.scene,
        session_summary=context.summary,
        persona_service=make_test_persona_service(),
        app_settings=make_test_settings(),
    )
    rendered = json.dumps(messages)
    assert "Location: The tower" in rendered and "Public tower" in rendered
    assert "PRIVATE PASSCODE" not in rendered


@pytest.mark.parametrize("damage", ["missing", "digest", "future", "audience", "schema"])
def test_opaque_or_mismatched_sidecars_never_authorize(db, damage):
    row = append(db)
    write_artifacts(db, row)
    if damage == "missing":
        db.execute("DELETE FROM memory_artifact_visibility")
    elif damage == "digest":
        db.execute("UPDATE memory_artifact_visibility SET payload_digest='wrong'")
    elif damage == "future":
        db.execute("UPDATE memory_artifact_visibility SET through_rowid=999")
    elif damage == "audience":
        db.execute(
            "UPDATE memory_artifact_visibility SET blocks_json=?",
            (json.dumps([{"text": "PRIVATE PASSCODE", "visibility": "restricted", "known_by": [42]}]),),
        )
    else:
        db.execute("UPDATE memory_artifact_visibility SET schema_version=999")
    db.commit()
    context = service().prompt_context(db, "c", {"session_id": "s"}, {"name": "Bob"}, "tower")
    assert not context.summary and not context.scene


def test_edit_regen_share_boundary_and_historical_reads_never_refresh(db, monkeypatch):
    first = append(db)
    write_artifacts(db, first)
    retained_user = append(db, "Where is the key?")
    discarded_answer = append(db, "A discarded answer with a future secret.")
    monkeypatch.setattr(memory, "generate_session_summary", lambda *a, **k: pytest.fail("Historical provider call"))
    edit = service().prompt_context(
        db,
        "c",
        {"session_id": "s"},
        {"name": "Mira"},
        "key",
        edited_user_rowid=retained_user,
    )
    assert edit.scope.through_rowid == retained_user - 1
    assert edit.summary and edit.scene
    write_artifacts(db, discarded_answer, summary="Discarded answer summary")
    regen = service().prompt_context(
        db,
        "c",
        {"session_id": "s"},
        {"name": "Mira"},
        "key",
        through_rowid=retained_user,
    )
    assert regen.scope.through_rowid == retained_user
    assert regen.summary == "" and regen.scene == ""


@pytest.mark.parametrize("race", ["append", "rewrite", "recreate"])
def test_final_validation_fences_artifact_races(db, race):
    row = append(db)
    write_artifacts(db, row)

    def recall(*args):
        if race == "append":
            append(db, "A harmless later turn.")
        elif race == "rewrite":
            db.execute("UPDATE messages SET content='Rewritten' WHERE id=?", (row,))
            db.commit()
        else:
            db.execute("UPDATE sessions SET created_at=99 WHERE chat_id='c' AND session_id='s'")
            db.commit()
        return MemoryBlock(channel="recall")

    context = service(recall).prompt_context(db, "c", {"session_id": "s"}, {"name": "Bob"}, "tower")
    assert bool(context.summary) is (race == "append")
    assert bool(context.scene) is (race == "append")
    assert context.scope.through_rowid == row


def test_scene_and_summary_model_paths_persist_strict_classification(db):
    from bridge.memory_artifact_store import read_scene_block, read_summary_block

    row = append(db, "The public tower; only Mira knows the passcode.")
    provider = make_test_provider_port(
        generate_backend=lambda *a, **k: json.dumps(
            {
                "state": {"location": "Tower", "facts": ["PRIVATE PASSCODE"]},
                "blocks": [classified("Location: Tower"), classified("PRIVATE PASSCODE", ("Mira",))],
            }
        )
    )
    scene_state.refresh_scene_state_now(
        db,
        "",
        "c",
        {"session_id": "s", "model_id": "m"},
        "Mira",
        provider_port=provider,
        app_settings=make_test_settings(),
    )
    assert read_scene_block(db, scope(db, "Bob", row)).text == "Location: Tower"
    summary_provider = make_test_provider_port(
        generate_backend=lambda *a, **k: json.dumps(
            {
                "blocks": [classified("They reached the public tower."), classified("PRIVATE PASSCODE", ("Mira",))],
            }
        )
    )
    result = memory.generate_session_summary_result(
        db,
        "c",
        {"session_id": "s", "model_id": "m"},
        force=True,
        durable=True,
        max_segments=1,
        provider_port=summary_provider,
        app_settings=make_test_settings(),
    )
    assert result.complete
    assert read_summary_block(db, scope(db, "Bob", row)).text == "They reached the public tower."


def test_populated_migration_twenty_replays_opaque_derived_coverage_from_canonical_source(tmp_path):
    connection = sqlite3.connect(":memory:")
    try:
        run_migrations(connection, tuple(m for m in SCHEMA_MIGRATIONS if m.version <= 20))
        connection.execute(
            "INSERT INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,"
            "world_file,created_at,updated_at) VALUES('c','s','Story','','m','','',1,1)"
        )
        row = append(connection, "PUBLIC CANONICAL SOURCE")
        store_segment(connection, next_source_segment(connection, "c", "s", "episodes"))
        connection.execute("INSERT INTO session_summaries VALUES('c','s','OPAQUE PREDECESSOR',?,1)", (row,))
        connection.execute(
            "INSERT INTO scene_states VALUES('c','s','{\"location\":\"OPAQUE PREDECESSOR\"}',?,1)", (row,)
        )
        connection.execute("UPDATE memory_jobs SET completed_version=dirty_version")
        connection.commit()
        run_migrations(connection, SCHEMA_MIGRATIONS)
        assert memory.get_session_summary(connection, "c", "s")[0] == "OPAQUE PREDECESSOR"
        assert next_source_segment(connection, "c", "s", "episodes") is not None
        assert not service().prompt_context(connection, "c", {"session_id": "s"}, {"name": "Bob"}, "source").summary
        calls = []

        def generate(*args, **kwargs):
            calls.append(json.dumps(args[2]))
            return json.dumps({"blocks": [classified("Classified canonical continuity.")]})

        result = memory.generate_session_summary_result(
            connection,
            "c",
            {"session_id": "s", "model_id": "m"},
            force=True,
            durable=True,
            max_segments=1,
            provider_port=make_test_provider_port(generate_backend=generate),
            app_settings=make_test_settings(home=tmp_path),
        )
        assert result.complete and calls
        assert "PUBLIC CANONICAL SOURCE" in calls[0] and "OPAQUE PREDECESSOR" not in calls[0]
        assert (
            service()
            .prompt_context(
                connection,
                "c",
                {"session_id": "s"},
                {"name": "Bob"},
                "source",
            )
            .summary
            == "Classified canonical continuity."
        )
    finally:
        connection.close()



@pytest.mark.parametrize("kind", ["summary", "scene"])
def test_final_validation_rejects_changed_classification_with_identical_parent_payload(db, kind):
    from bridge.memory_artifact_store import store_artifact_visibility

    row = append(db)
    write_artifacts(db, row)

    def recall(*_args):
        with write_transaction(db):
            store_artifact_visibility(db, "c", "s", kind, [classified("A replacement block accepted during recall")])
        return MemoryBlock(channel="recall")

    context = service(recall).prompt_context(db, "c", {"session_id": "s"}, {"name": "Bob"}, "tower")
    assert getattr(context, kind) == ""
    assert getattr(context, "scene" if kind == "summary" else "summary")
