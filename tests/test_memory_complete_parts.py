"""Complete canonical parts are private until their entire source row is accepted."""

import json
import sqlite3

import pytest
from application_test_setup import make_test_provider_port
from settings_test_support import make_test_settings
from test_story_memory_scope import append
from test_story_memory_scope import db as db
from test_story_memory_scope import forbid_real_hindsight_client as forbid_real_hindsight_client

from bridge import memory_backend, npc_extraction, persona_sync
from bridge.memory import get_session_summary
from bridge.memory_curator import get_curated_memory_state
from bridge.memory_store import claim_jobs
from bridge.memory_workers import run_memory_claim
from bridge.npc_repository import get_npc_extraction_coverage, list_npc_entities, load_npc_fields
from bridge.scene_state import get_scene_state

MARKERS = ("HEAD_MARKER", "MIDDLE_MARKER", "TAIL_MARKER")
SESSION = {"session_id": "s", "model_id": "m", "persona_id": "", "system_prompt": ""}
FIELDS = {"name": "Bob"}


@pytest.fixture(autouse=True)
def isolate_native_settings(monkeypatch):
    monkeypatch.setattr(npc_extraction, "persona_name", lambda *a, **k: "Synthetic User")
    monkeypatch.setattr(
        persona_sync,
        "_native_settings",
        lambda *a, **k: pytest.fail("Complete-part tests must fail before native Persona access"),
    )
    monkeypatch.setattr(memory_backend, "_retain_with_client", lambda *a, **k: pytest.fail("Unexpected retain"))


def long_source():
    return "HEAD_MARKER" + "x" * (74000 - 11) + "MIDDLE_MARKER" + "y" * (150000 - 74000 - 13 - 11) + "TAIL_MARKER"


def output_for(layer, text):
    found = [marker for marker in MARKERS if marker in text]
    blocks = [{"text": " ".join(found) or "No marker yet", "visibility": "shared", "known_by": []}]
    if layer == "summary":
        return json.dumps({"blocks": blocks})
    if layer == "scene":
        return json.dumps({"state": {"facts": found or ["No marker yet"]}, "blocks": blocks})
    if layer == "curator":
        return json.dumps(
            {"memories": [{"key": marker.lower(), "kind": "fact", "text": marker, "confidence": 1} for marker in found]}
        )
    return json.dumps(
        {
            "npcs": [
                {
                    "name": "Ivo",
                    "aliases": [],
                    "operations": [
                        {
                            "field": "status",
                            "op": "append",
                            "value": marker,
                            "mode": "mutable",
                            "visibility": "restricted",
                            "known_by": ["Bob"],
                        }
                        for marker in found
                    ],
                }
            ]
            if found
            else []
        }
    )


def backend(db, layer, supplied, mutate=None):
    def generate(api_key, model, messages, **kwargs):
        assert not db.in_transaction
        value = messages[-1]["content"]
        supplied.append(value.split("\n\nCanonical source part:\n", 1)[-1])
        if mutate:
            mutate()
        return output_for(layer, value)

    return make_test_provider_port(generate_backend=generate)


def run(db, layer, settings, port):
    db.execute("UPDATE memory_jobs SET next_attempt_at=0 WHERE layer=?", (layer,))
    db.commit()
    claim = claim_jobs(db, layers=(layer,))[0]
    return run_memory_claim(db, claim, SESSION, FIELDS, provider_port=port, app_settings=settings)


def published(db, layer):
    if layer == "summary":
        return get_session_summary(db, "c", "s")
    if layer == "scene":
        value, through = get_scene_state(db, "c", "s")
    elif layer == "curator":
        value, through = get_curated_memory_state(db, "c", "s")
    else:
        value = [
            {key: state.value for key, state in load_npc_fields(db, entity.npc_id).items()}
            for entity in list_npc_entities(db, "c", "s")
        ]
        through = get_npc_extraction_coverage(db, "c", "s")
    return json.dumps(value), through


@pytest.mark.parametrize("layer", ["summary", "scene", "curator", "npc"])
def test_150000_char_row_resumes_without_partial_publication(db, tmp_path, layer):
    settings = make_test_settings(home=tmp_path)
    db.execute("INSERT INTO meta(key,value) VALUES('memory_mode:c','on')")
    db.commit()
    source = long_source()
    assert len(source) == 150000
    row = append(db, source)
    supplied = []
    result = run(db, layer, settings, backend(db, layer, supplied))
    assert result == "deferred"
    assert len(supplied) == 8
    assert "".join(supplied) == source[:96000]
    assert published(db, layer)[1] == 0
    assert db.execute("SELECT covered_id FROM memory_layer_state WHERE layer=?", (layer,)).fetchone() == (0,)

    # A new connection stands in for a worker process restart; drafts are SQLite state.
    reopened = sqlite3.connect(":memory:")
    db.backup(reopened)
    result = run(reopened, layer, settings, backend(reopened, layer, supplied))
    assert result == "complete"
    assert len(supplied) == 13 and "".join(supplied) == source
    value, through = published(reopened, layer)
    assert through == row and all(marker in value for marker in MARKERS)
    assert reopened.execute("SELECT covered_id FROM memory_layer_state WHERE layer=?", (layer,)).fetchone() == (row,)
    reopened.close()


def test_draft_and_source_acceptance_roll_back_together(db, tmp_path):
    append(db, "HEAD_MARKER")
    db.execute(
        "CREATE TRIGGER reject_summary_part BEFORE INSERT ON memory_segments "
        "WHEN NEW.layer='summary' BEGIN SELECT RAISE(ABORT,'synthetic acceptance failure'); END"
    )
    db.commit()
    result = run(db, "summary", make_test_settings(home=tmp_path), backend(db, "summary", []))
    assert result == "work_failed"
    assert published(db, "summary") == ("", 0)
    assert db.execute("SELECT count(*) FROM memory_segments WHERE layer='summary'").fetchone() == (0,)
    assert db.execute("SELECT draft_source_id FROM memory_layer_state WHERE layer='summary'").fetchone() == ("",)


def test_edit_during_part_rejects_output_and_does_not_ack(db, tmp_path):
    row = append(db, "HEAD_MARKER")

    def rewrite():
        db.execute("UPDATE messages SET content='Replacement' WHERE id=?", (row,))
        db.commit()

    result = run(db, "scene", make_test_settings(home=tmp_path), backend(db, "scene", [], rewrite))
    assert result == "stale_source"
    assert published(db, "scene")[1] == 0
    assert db.execute("SELECT count(*) FROM memory_segments WHERE layer='scene' AND valid=1").fetchone() == (0,)


def test_append_during_part_keeps_progress_and_new_work_pending(db, tmp_path):
    row = append(db, "HEAD_MARKER" + "x" * 15000)
    supplied = []
    mutated = False

    def later():
        nonlocal mutated
        if not mutated:
            mutated = True
            append(db, "A later accepted turn")

    result = run(db, "summary", make_test_settings(home=tmp_path), backend(db, "summary", supplied, later))
    assert result == "complete"
    assert "".join(supplied) == "HEAD_MARKER" + "x" * 15000
    assert published(db, "summary")[1] == row
    dirty, completed = db.execute(
        "SELECT dirty_version,completed_version FROM memory_jobs WHERE layer='summary'"
    ).fetchone()
    assert dirty > completed


@pytest.mark.parametrize("layer", ["summary", "scene", "curator", "npc"])
def test_last_turn_rewrite_restores_complete_prefix_checkpoint(db, tmp_path, layer):
    settings = make_test_settings(home=tmp_path)
    db.execute("INSERT INTO meta(key,value) VALUES('memory_mode:c','on')")
    db.commit()
    for marker in MARKERS:
        row = append(db, marker)
    supplied = []
    assert run(db, layer, settings, backend(db, layer, supplied)) == "complete"
    assert all(marker in published(db, layer)[0] for marker in MARKERS)
    db.execute("UPDATE messages SET content='Replacement' WHERE id=?", (row,))
    db.commit()
    supplied.clear()
    assert run(db, layer, settings, backend(db, layer, supplied)) == "complete"
    assert supplied == ["Replacement"]
    value, through = published(db, layer)
    assert through == row and "HEAD_MARKER" in value and "MIDDLE_MARKER" in value and "TAIL_MARKER" not in value


@pytest.mark.parametrize("layer", ["summary", "scene", "curator", "npc"])
def test_malformed_part_never_advances_coverage(db, tmp_path, layer):
    db.execute("INSERT INTO meta(key,value) VALUES('memory_mode:c','on')")
    db.commit()
    append(db, "HEAD_MARKER")
    result = run(
        db,
        layer,
        make_test_settings(home=tmp_path),
        make_test_provider_port(generate_backend=lambda *a, **k: "{bad JSON"),
    )
    assert result == "work_failed"
    assert published(db, layer)[1] == 0
    assert db.execute("SELECT count(*) FROM memory_segments WHERE layer=? AND valid=1", (layer,)).fetchone() == (0,)


def test_unbounded_episodic_compatibility_source_is_rejected_before_inference(db, tmp_path):
    from bridge.episodic_extraction import extract_episodic_memories_result

    row = append(db, long_source())
    with pytest.raises(ValueError, match=r"bounded|part|large"):
        extract_episodic_memories_result(
            db,
            "c",
            SESSION,
            source_text=long_source(),
            source_start_rowid=row,
            source_end_rowid=row,
            provider_port=make_test_provider_port(generate_backend=lambda *a, **k: pytest.fail("Unsafe input")),
            app_settings=make_test_settings(home=tmp_path),
        )


def test_populated_pre_complete_schema_replays_every_derived_layer(tmp_path):
    from bridge.migrations import run_migrations
    from bridge.schema import SCHEMA_MIGRATIONS, initialize_database_schema

    connection = sqlite3.connect(":memory:")
    run_migrations(connection, tuple(item for item in SCHEMA_MIGRATIONS if item.version <= 22))
    connection.execute(
        "INSERT INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,"
        "world_file,created_at,updated_at) VALUES('c','s','Story','','m','','',1,1)"
    )
    connection.commit()
    row = append(connection, long_source())
    connection.execute("UPDATE memory_layer_state SET covered_id=?", (row,))
    connection.execute("UPDATE memory_jobs SET completed_version=dirty_version")
    connection.execute("INSERT INTO session_summaries VALUES('c','s','Legacy incomplete summary',?,1)", (row,))
    connection.execute("INSERT INTO scene_states VALUES('c','s','{\"location\":\"Legacy scene\"}',?,1)", (row,))
    connection.execute("INSERT INTO npc_extraction_state VALUES('c','s',?,1)", (row,))
    connection.commit()
    initialize_database_schema(connection)
    assert connection.execute(
        "SELECT layer,covered_id FROM memory_layer_state WHERE layer IN "
        "('summary','scene','curator','npc') ORDER BY layer"
    ).fetchall() == [("curator", 0), ("npc", 0), ("scene", 0), ("summary", 0)]
    assert connection.execute(
        "SELECT count(*) FROM memory_jobs WHERE layer IN "
        "('summary','scene','curator','npc') AND dirty_version>completed_version"
    ).fetchone() == (4,)
    supplied = []
    settings = make_test_settings(home=tmp_path)
    assert run(connection, "summary", settings, backend(connection, "summary", supplied)) == "deferred"
    assert published(connection, "summary") == ("Legacy incomplete summary", row)
    assert run(connection, "summary", settings, backend(connection, "summary", supplied)) == "complete"
    assert "".join(supplied) == long_source()
    assert all(marker in published(connection, "summary")[0] for marker in MARKERS)
    connection.close()


@pytest.mark.parametrize("layer", ["summary", "scene", "curator", "npc"])
def test_manual_refresh_uses_the_same_complete_part_backlog(db, tmp_path, layer):
    from bridge.memory import generate_session_summary_result
    from bridge.memory_curator import curate_memory_now
    from bridge.npc_extraction import refresh_npc_state_now
    from bridge.scene_state import refresh_scene_state_now

    db.execute("INSERT INTO meta(key,value) VALUES('memory_mode:c','on')")
    db.commit()
    row = append(db, long_source())
    supplied = []
    kwargs = {"provider_port": backend(db, layer, supplied), "app_settings": make_test_settings(home=tmp_path)}

    def refresh():
        if layer == "summary":
            return generate_session_summary_result(db, "c", SESSION, force=True, **kwargs)
        if layer == "scene":
            return refresh_scene_state_now(db, "", "c", SESSION, "Bob", **kwargs)
        if layer == "curator":
            return curate_memory_now(db, "", "c", SESSION, "Bob", **kwargs)
        return refresh_npc_state_now(db, "c", SESSION, FIELDS, **kwargs)

    refresh()
    assert len(supplied) == 8 and published(db, layer)[1] == 0
    db.execute("UPDATE memory_jobs SET next_attempt_at=0 WHERE layer=?", (layer,))
    db.commit()
    refresh()
    assert "".join(supplied) == long_source()
    value, through = published(db, layer)
    assert through == row and all(marker in value for marker in MARKERS)


def test_late_draft_write_failure_rolls_back_publication_and_part(db, tmp_path):
    append(db, "HEAD_MARKER")
    db.execute(
        "CREATE TRIGGER reject_summary_draft BEFORE UPDATE OF draft_json ON memory_layer_state "
        "WHEN NEW.layer='summary' AND NEW.draft_source_id<>'' "
        "BEGIN SELECT RAISE(ABORT,'synthetic draft failure'); END"
    )
    db.commit()
    assert run(db, "summary", make_test_settings(home=tmp_path), backend(db, "summary", [])) == "work_failed"
    assert published(db, "summary") == ("", 0)
    assert db.execute("SELECT count(*) FROM memory_segments WHERE layer='summary'").fetchone() == (0,)
    assert db.execute("SELECT count(*) FROM memory_artifact_visibility").fetchone() == (0,)


@pytest.mark.parametrize("layer", ["summary", "scene", "curator", "npc"])
def test_newer_publication_during_inference_rejects_the_obsolete_part(db, tmp_path, layer):
    from bridge.memory_draft_publish import publish_derived
    from bridge.sqlite_store import write_transaction

    db.execute("INSERT INTO meta(key,value) VALUES('memory_mode:c','on')")
    db.commit()
    append(db, "HEAD_MARKER")
    after = []

    def replace_published():
        with write_transaction(db):
            if layer == "summary":
                payload = {"blocks": [{"text": "NEWER", "visibility": "shared", "known_by": []}]}
            elif layer == "scene":
                payload = {
                    "state": {"facts": ["NEWER"]},
                    "blocks": [{"text": "NEWER", "visibility": "shared", "known_by": []}],
                }
            elif layer == "curator":
                payload = {"memories": [{"key": "newer", "text": "NEWER", "kind": "fact", "confidence": 1}]}
            else:
                payload = {
                    "primary_name": "Bob",
                    "user_name": "Synthetic User",
                    "npcs": [
                        {
                            "name": "Ivo",
                            "aliases": [],
                            "operations": [
                                {
                                    "field": "status",
                                    "op": "set",
                                    "value": "NEWER",
                                    "mode": "mutable",
                                    "visibility": "shared",
                                    "known_by": [],
                                }
                            ],
                        }
                    ],
                }
            publish_derived(db, "c", "s", layer, payload, 1)
        after.append(published(db, layer))

    result = run(db, layer, make_test_settings(home=tmp_path), backend(db, layer, [], replace_published))
    assert result == "stale_source"
    assert published(db, layer) == after[0]
    assert db.execute("SELECT count(*) FROM memory_segments WHERE layer=?", (layer,)).fetchone()[0] == 0


@pytest.mark.parametrize("state", ["partial", "checkpoint"])
def test_clear_invalidates_persisted_curator_drafts_before_later_claims(db, tmp_path, state):
    from bridge.memory_backend import clear_curated_memory_state

    db.execute("INSERT INTO meta(key,value) VALUES('memory_mode:c','on')")
    db.commit()
    row = append(db, long_source() if state == "partial" else "HEAD_MARKER")
    settings = make_test_settings(home=tmp_path)
    assert run(db, "curator", settings, backend(db, "curator", [])) == (
        "deferred" if state == "partial" else "complete"
    )
    assert db.execute("SELECT draft_json FROM memory_layer_state WHERE layer='curator'").fetchone()[0]
    clear_curated_memory_state(db, "c", "s")
    assert published(db, "curator")[1] == 0
    assert db.execute("SELECT draft_json FROM memory_layer_state WHERE layer='curator'").fetchone() == ("",)
    assert db.execute("SELECT count(*) FROM memory_layer_checkpoints WHERE layer='curator'").fetchone() == (0,)
    supplied = []

    def empty(_key, _model, messages, **kwargs):
        prompt = messages[-1]["content"]
        if not supplied:
            assert "head_marker" not in prompt.split("\nSource role:", 1)[0]
        supplied.append(prompt.split("\n\nCanonical source part:\n", 1)[1])
        return '{"memories":[]}'

    port = make_test_provider_port(generate_backend=empty)
    outcome = run(db, "curator", settings, port)
    if outcome == "deferred":
        outcome = run(db, "curator", settings, port)
    assert outcome == "complete"
    assert supplied[0].startswith("HEAD_MARKER")
    assert "HEAD_MARKER" not in published(db, "curator")[0]
    assert published(db, "curator")[1] == row
