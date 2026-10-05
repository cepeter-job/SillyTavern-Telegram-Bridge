"""Review regressions for retained NPC history and persistent artifact Clear."""

import json

import pytest
from application_test_setup import ensure_application_extensions, make_test_provider_port
from memory_runtime_test_support import isolated_memory_runtime as isolated_memory_runtime
from settings_test_support import make_test_settings
from test_memory_complete_parts import FIELDS, SESSION, backend, long_source, published, run
from test_story_memory_scope import append
from test_story_memory_scope import db as db

from bridge import memory, scene_state
from bridge.memory_scope_store import resolve_memory_scope
from bridge.memory_store import pending_memory_invalidation
from bridge.npc_repository import set_npc_extraction_coverage
from bridge.npc_service import NpcService
from bridge.npc_types import NpcExtractionGroup, NpcOperation
from bridge.sqlite_store import write_transaction


def test_npc_no_checkpoint_keeps_valid_prefix_during_failed_and_partial_replay(db, tmp_path):
    settings = make_test_settings(home=tmp_path)
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
            primary_name="Bob",
            user_name="Synthetic User",
        )
    with write_transaction(db):
        set_npc_extraction_coverage(db, "c", "s", second, 3)
    old_scope = resolve_memory_scope(db, "c", SESSION, FIELDS)
    original_voice = db.execute("SELECT * FROM npc_field_history WHERE source_rowid=?", (first,)).fetchall()
    db.execute("UPDATE messages SET content=? WHERE id=?", ("z" * 149970 + "Maya feels replacement anxiety.", second))
    db.commit()
    assert db.execute("SELECT count(*) FROM memory_layer_checkpoints WHERE layer='npc'").fetchone() == (0,)
    observed = []

    def context(scope):
        return npc.context_for_prompt(db, "c", SESSION, FIELDS, "Maya", [], memory_scope=scope)

    def extract(_key, _model, messages, **kwargs):
        assert not db.in_transaction
        prompt = messages[-1]["content"]
        part = prompt.split("\n\nCanonical source part:\n", 1)[1]
        observed.append(context(old_scope))
        # No previous current-state value may leak backward into an earlier source part.
        if "Source role: user; message 1;" in prompt:
            existing = prompt.split("Existing NPC state:\n", 1)[1].split("\nAccepted private", 1)[0]
            assert "soft" not in existing and "calm" not in existing and "anxiety" not in existing
        operations = []
        if "Maya feels replacement anxiety." in part:
            operations = [
                {
                    "field": "mood",
                    "op": "set",
                    "value": "replacement anxiety",
                    "mode": "mutable",
                    "visibility": "shared",
                    "known_by": [],
                }
            ]
        # Intentionally never re-emit the already accepted voice.
        return json.dumps({"npcs": [{"name": "Maya", "aliases": [], "operations": operations}] if operations else []})

    port = make_test_provider_port(generate_backend=extract)
    assert run(db, "npc", settings, port) == "deferred"
    assert observed and all("soft" in value and "calm" not in value and "anxiety" not in value for value in observed)
    assert "soft" in context(old_scope)
    assert db.execute("SELECT * FROM npc_field_history WHERE source_rowid=?", (first,)).fetchall() == original_voice

    def fail(*args, **kwargs):
        assert "soft" in context(old_scope)
        raise RuntimeError("Synthetic extraction interruption")

    assert run(db, "npc", settings, make_test_provider_port(generate_backend=fail)) == "work_failed"
    assert "soft" in context(old_scope) and "anxiety" not in context(old_scope)
    assert run(db, "npc", settings, port) == "complete"
    assert db.execute("SELECT * FROM npc_field_history WHERE source_rowid=?", (first,)).fetchall() == original_voice
    old = context(old_scope)
    fresh = context(resolve_memory_scope(db, "c", SESSION, FIELDS))
    assert "soft" in old and "calm" not in old and "replacement anxiety" not in old
    assert "soft" in fresh and "replacement anxiety" in fresh and "calm" not in fresh
    assert pending_memory_invalidation(db, "c", "s", "npc") is None


def clear(db, layer):
    ensure_application_extensions()
    if layer == "summary":
        memory.clear_session_summary(db, "c", "s")
    else:
        scene_state.clear_scene_state(db, "c", "s")


def manual(db, layer, settings, port):
    if layer == "summary":
        return memory.generate_session_summary_result(
            db,
            "c",
            SESSION,
            force=True,
            provider_port=port,
            app_settings=settings,
        )
    return scene_state.refresh_scene_state_now(
        db,
        "",
        "c",
        SESSION,
        "Bob",
        provider_port=port,
        app_settings=settings,
    )


@pytest.mark.parametrize("layer", ["summary", "scene"])
def test_clear_without_parent_fences_inflight_result_and_allows_manual_replay(db, tmp_path, layer):
    settings = make_test_settings(home=tmp_path)
    append(db, "HEAD_MARKER")
    assert published(db, layer)[1] == 0
    before = db.execute("SELECT id,role,content FROM messages").fetchall()
    supplied = []
    manual(db, layer, settings, backend(db, layer, supplied, lambda: clear(db, layer)))
    assert supplied == ["HEAD_MARKER"]
    assert published(db, layer)[1] == 0
    assert db.execute("SELECT draft_json FROM memory_layer_state WHERE layer=?", (layer,)).fetchone() == ("",)
    assert db.execute("SELECT count(*) FROM memory_layer_checkpoints WHERE layer=?", (layer,)).fetchone() == (0,)
    db.execute("UPDATE memory_jobs SET next_attempt_at=0 WHERE layer=?", (layer,))
    db.commit()
    manual(db, layer, settings, backend(db, layer, []))
    assert published(db, layer)[1] == 1
    assert db.execute("SELECT id,role,content FROM messages").fetchall() == before


@pytest.mark.parametrize("layer", ["summary", "scene"])
@pytest.mark.parametrize("state", ["partial", "checkpoint"])
def test_clear_retires_saved_progress_and_manual_refresh_replays_without_append(db, tmp_path, layer, state):
    settings = make_test_settings(home=tmp_path)
    append(db, long_source() if state == "partial" else "HEAD_MARKER")
    assert run(db, layer, settings, backend(db, layer, [])) == ("deferred" if state == "partial" else "complete")
    before = db.execute("SELECT id,role,content FROM messages").fetchall()
    clear(db, layer)
    assert published(db, layer)[1] == 0
    assert db.execute("SELECT draft_json FROM memory_layer_state WHERE layer=?", (layer,)).fetchone() == ("",)
    assert db.execute("SELECT count(*) FROM memory_layer_checkpoints WHERE layer=?", (layer,)).fetchone() == (0,)
    assert db.execute("SELECT count(*) FROM memory_segments WHERE layer=? AND valid=1", (layer,)).fetchone() == (0,)
    assert db.execute("SELECT dirty_version>completed_version FROM memory_jobs WHERE layer=?", (layer,)).fetchone() == (
        1,
    )
    supplied = []

    def extract(_key, _model, messages, **kwargs):
        prompt = messages[-1]["content"]
        if not supplied:
            prior = prompt.split("\nSource role:", 1)[0]
            assert "HEAD_MARKER" not in prior
        supplied.append(prompt.split("\n\nCanonical source part:\n", 1)[1])
        blocks = [{"text": "Rebuilt", "visibility": "shared", "known_by": []}]
        return json.dumps(
            {"blocks": blocks} if layer == "summary" else {"state": {"facts": ["Rebuilt"]}, "blocks": blocks}
        )

    port = make_test_provider_port(generate_backend=extract)
    manual(db, layer, settings, port)
    if state == "partial":
        db.execute("UPDATE memory_jobs SET next_attempt_at=0 WHERE layer=?", (layer,))
        db.commit()
        manual(db, layer, settings, port)
    assert supplied[0].startswith("HEAD_MARKER")
    assert "HEAD_MARKER" not in published(db, layer)[0] and published(db, layer)[1] == 1
    assert db.execute("SELECT id,role,content FROM messages").fetchall() == before


def test_summary_clear_retires_its_scene_hook_in_the_same_transaction(db, tmp_path):
    settings = make_test_settings(home=tmp_path)
    append(db, "HEAD_MARKER")
    for layer in ("summary", "scene"):
        assert run(db, layer, settings, backend(db, layer, [])) == "complete"
    with pytest.raises(RuntimeError, match="rollback"), write_transaction(db):
        clear(db, "summary")
        assert published(db, "summary")[1] == published(db, "scene")[1] == 0
        raise RuntimeError("rollback")
    assert published(db, "summary")[1] == published(db, "scene")[1] == 1
    clear(db, "summary")
    for layer in ("summary", "scene"):
        assert published(db, layer)[1] == 0
        assert db.execute("SELECT draft_json FROM memory_layer_state WHERE layer=?", (layer,)).fetchone() == ("",)
        assert db.execute("SELECT count(*) FROM memory_layer_checkpoints WHERE layer=?", (layer,)).fetchone() == (0,)
        assert db.execute(
            "SELECT dirty_version>completed_version FROM memory_jobs WHERE layer=?", (layer,)
        ).fetchone() == (1,)
