"""Final integration regressions at real archival and classified extraction boundaries."""

import json
import sqlite3
from types import SimpleNamespace

import pytest
from application_test_setup import make_test_provider_port
from memory_runtime_test_support import isolated_memory_runtime as isolated_memory_runtime
from settings_test_support import make_test_settings

from bridge import memory, memory_backend
from bridge.memory_artifact_store import read_scene_block
from bridge.memory_curator import get_curated_memory_state
from bridge.memory_fact_store import load_source
from bridge.memory_scope_store import resolve_memory_scope
from bridge.memory_store import claim_jobs, next_source_segment, purge_external_memory
from bridge.memory_workers import run_memory_claim
from bridge.scene_state import get_scene_state, scene_state_text
from bridge.sqlite_store import db_connect

SESSION = {"session_id": "s", "model_id": "m", "persona_id": "", "system_prompt": ""}
FIELDS = {"name": "Bob"}


class Archive:
    def __init__(self):
        self.documents = self
        self.remote = {}
        self.retained = []
        self.deleted = []
        self.on_retain = None

    def retain(self, **kwargs):
        self.retained.append(kwargs)
        if self.on_retain:
            self.on_retain(kwargs)
        self.remote[kwargs["document_id"]] = kwargs

    async def list_documents(self, **kwargs):
        items = []
        for document_id, payload in self.remote.items():
            if kwargs.get("tags") and not set(kwargs["tags"]).intersection(payload["tags"]):
                continue
            if kwargs.get("q") and kwargs["q"] not in document_id:
                continue
            items.append({"id": document_id})
        offset = kwargs.get("offset", 0)
        return SimpleNamespace(items=items[offset : offset + kwargs["limit"]], total=len(items))

    async def delete_document(self, **kwargs):
        self.deleted.append(kwargs["document_id"])
        self.remote.pop(kwargs["document_id"], None)

    def close(self):
        pass

    async def aclose(self):
        pass


@pytest.fixture
def runtime(tmp_path, monkeypatch, isolated_memory_runtime):
    settings = make_test_settings(home=tmp_path, db_file=tmp_path / "integration.sqlite")
    archive = Archive()
    monkeypatch.setattr(memory_backend, "hindsight_client", lambda **kwargs: archive)
    db = db_connect(app_settings=settings)
    db.execute(
        "INSERT INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,"
        "world_file,created_at,updated_at) VALUES('c','s','Story','','m','','',1,1)"
    )
    db.commit()
    yield settings, db, archive
    db.close()


def append(db, text):
    row = db.execute(
        "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('c','s','user',?,3)", (text,)
    )
    db.commit()
    return row.lastrowid


def run(db, settings, layer="hindsight", provider=None):
    db.execute("UPDATE memory_jobs SET next_attempt_at=0 WHERE layer=?", (layer,))
    db.commit()
    claim = claim_jobs(db, layers=(layer,))[0]
    return run_memory_claim(db, claim, SESSION, FIELDS, provider_port=provider, app_settings=settings)


def seed_or_worker(route, db, settings):
    if route == "seed":
        return memory_backend.seed_session_memory_now(db, "c", SESSION, app_settings=settings)
    return run(db, settings)


def cleanup(db, settings):
    assert memory_backend.cleanup_retired_memory_documents(db, "c", "s", app_settings=settings)


def change_source(db, row, change):
    if change == "rewrite":
        db.execute("UPDATE messages SET content='Replacement' WHERE id=?", (row,))
    elif change == "purge":
        purge_external_memory(db, "c", "s", purge_epoch=1)
    else:
        db.execute("DELETE FROM sessions WHERE chat_id='c' AND session_id='s'")
        if change == "recreate":
            db.execute(
                "INSERT INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,"
                "world_file,created_at,updated_at) VALUES('c','s','Replacement','','m','','',99,99)"
            )
            append(db, "New incarnation")
    db.commit()


@pytest.mark.parametrize("route", ["worker", "seed"])
@pytest.mark.parametrize("change", ["rewrite", "delete", "recreate", "purge"])
def test_raw_late_completion_is_retired_without_stale_mapping(runtime, route, change):
    settings, db, archive = runtime
    row = append(db, "Old canonical source")

    def late(kwargs):
        other = sqlite3.connect(settings.db_file)
        try:
            change_source(other, row, change)
        finally:
            other.close()

    archive.on_retain = late
    assert seed_or_worker(route, db, settings) == ("degraded" if route == "seed" else "stale_source")
    old_id = archive.retained[0]["document_id"]
    assert db.execute("SELECT 1 FROM hindsight_documents WHERE document_id=?", (old_id,)).fetchone() is None
    assert db.execute("SELECT deleted FROM memory_retired_documents WHERE document_id=?", (old_id,)).fetchone() == (0,)
    assert load_source(db, old_id) is None
    if change == "delete":
        assert db.execute("SELECT count(*) FROM memory_jobs").fetchone() == (0,)
    archive.on_retain = None
    cleanup(db, settings)
    assert old_id not in archive.remote
    if change == "recreate":
        assert run(db, settings) == "complete"
        new_id = archive.retained[-1]["document_id"]
        assert new_id != old_id and new_id in archive.remote
        assert memory_backend._retirement_would_delete_current_source(db, new_id)


@pytest.mark.parametrize("route", ["worker", "seed"])
def test_raw_late_completion_reopens_already_cleaned_retirement(runtime, route):
    settings, db, archive = runtime
    row = append(db, "Old canonical source")

    def late(kwargs):
        change_source(db, row, "rewrite")
        cleanup(db, settings)
        assert db.execute(
            "SELECT deleted FROM memory_retired_documents WHERE document_id=?", (kwargs["document_id"],)
        ).fetchone() == (1,)

    archive.on_retain = late
    assert seed_or_worker(route, db, settings) == ("degraded" if route == "seed" else "stale_source")
    old_id = archive.retained[0]["document_id"]
    assert old_id in archive.remote
    assert db.execute(
        "SELECT deleted,next_attempt_at FROM memory_retired_documents WHERE document_id=?", (old_id,)
    ).fetchone() == (0, 0)
    archive.on_retain = None
    cleanup(db, settings)
    assert old_id not in archive.remote


@pytest.mark.parametrize("route", ["worker", "seed"])
def test_pending_raw_attempt_is_not_accepted_and_retries_after_restart(runtime, route):
    settings, db, archive = runtime
    row = append(db, "A complete source")

    observations = []

    def uncertain(kwargs):
        document_id = kwargs["document_id"]
        observations.append(
            (
                db.in_transaction,
                load_source(db, document_id),
                memory_backend.memory_document_is_current(db, "c", "s", document_id),
                next_source_segment(db, "c", "s", "hindsight").document_id,
                db.execute("SELECT covered_id FROM memory_layer_state WHERE layer='hindsight'").fetchone(),
                memory_backend._retirement_would_delete_current_source(db, document_id),
            )
        )
        archive.remote[document_id] = kwargs
        raise RuntimeError("Uncertain remote success")

    archive.on_retain = uncertain
    assert seed_or_worker(route, db, settings) == ("degraded" if route == "seed" else "retain_failed")
    assert observations == [(False, None, False, archive.retained[0]["document_id"], (0,), True)]
    old_id = archive.retained[0]["document_id"]
    archive.on_retain = None
    reopened = db_connect(app_settings=settings)
    try:
        assert seed_or_worker(route, reopened, settings) == ("ready" if route == "seed" else "complete")
        assert archive.retained[-1]["document_id"] == old_id
        assert reopened.execute("SELECT covered_id FROM memory_layer_state WHERE layer='hindsight'").fetchone() == (
            row,
        )
        assert load_source(reopened, old_id) is not None
        assert reopened.execute("SELECT kind FROM hindsight_documents WHERE document_id=?", (old_id,)).fetchone() == (
            "source_segment",
        )
    finally:
        reopened.close()


def test_failed_pending_prefix_reuses_identity_after_unrelated_suffix_rewrite(runtime):
    settings, db, archive = runtime
    first = append(db, "Prefix")
    suffix = append(db, "Suffix")

    def fail(kwargs):
        raise RuntimeError("Synthetic retain outage")

    archive.on_retain = fail
    assert run(db, settings) == "retain_failed"
    old_id = archive.retained[0]["document_id"]
    db.execute("UPDATE messages SET content='Replacement suffix' WHERE id=?", (suffix,))
    db.commit()
    assert next_source_segment(db, "c", "s", "hindsight").document_id == old_id
    archive.on_retain = None
    assert run(db, settings) == "complete"
    assert archive.retained[1]["document_id"] == old_id
    assert [item["content"] for item in archive.retained[1:]] == ["Prefix", "Replacement suffix"]
    assert db.execute(
        "SELECT count(*) FROM memory_segments WHERE layer='hindsight' AND start_id=?", (first,)
    ).fetchone() == (1,)


@pytest.mark.parametrize("route", ["worker", "seed"])
def test_uncertain_raw_write_after_mutation_still_retires(runtime, route):
    settings, db, archive = runtime
    row = append(db, "Obsolete")

    def uncertain(kwargs):
        archive.remote[kwargs["document_id"]] = kwargs
        change_source(db, row, "rewrite")
        raise RuntimeError("Remote succeeded before transport failure")

    archive.on_retain = uncertain
    assert seed_or_worker(route, db, settings) == ("degraded" if route == "seed" else "stale_source")
    old_id = archive.retained[0]["document_id"]
    assert db.execute("SELECT deleted FROM memory_retired_documents WHERE document_id=?", (old_id,)).fetchone() == (0,)
    archive.on_retain = None
    cleanup(db, settings)
    assert old_id not in archive.remote


@pytest.mark.parametrize("change", ["lease", "mode"])
def test_raw_interruption_keeps_current_attempt_and_replacement_ownership(runtime, change):
    settings, db, archive = runtime
    append(db, "Current source")

    def interrupt(kwargs):
        if change == "lease":
            db.execute(
                "UPDATE memory_jobs SET lease_token='replacement',lease_deadline=9999999999 WHERE layer='hindsight'"
            )
        else:
            db.execute("INSERT INTO meta(key,value) VALUES('memory_mode:c','off')")
        db.commit()

    archive.on_retain = interrupt
    assert run(db, settings) == ("stale_source" if change == "lease" else "disabled")
    document_id = archive.retained[0]["document_id"]
    assert load_source(db, document_id) is None
    assert db.execute("SELECT 1 FROM memory_retired_documents WHERE document_id=?", (document_id,)).fetchone() is None
    assert next_source_segment(db, "c", "s", "hindsight").document_id == document_id
    assert db.execute("SELECT covered_id FROM memory_layer_state WHERE layer='hindsight'").fetchone() == (0,)
    if change == "lease":
        assert db.execute("SELECT lease_token FROM memory_jobs WHERE layer='hindsight'").fetchone() == ("replacement",)


def curator_provider(supplied, callback=None):
    def generate(api_key, model, messages, **kwargs):
        user = messages[-1]["content"]
        previous_text = user.split("Previous curated memories:\n", 1)[1].split("\nSource role:", 1)[0]
        previous = json.loads(previous_text)
        source = user.split("Canonical source part:\n", 1)[1]
        supplied.append((previous, source))
        items = list(previous.get("memories", []))
        for token in ("OLD", "NEW"):
            if token in source and not any(item["key"] == token.lower() for item in items):
                items.append({"key": token.lower(), "text": token, "kind": "fact", "confidence": 1})
        if callback:
            callback()
        return json.dumps({"memories": items})

    return make_test_provider_port(generate_backend=generate)


def test_public_purge_preserves_curator_through_next_real_publication(runtime):
    settings, db, archive = runtime
    old = append(db, "OLD")
    supplied = []
    provider = curator_provider(supplied)
    assert run(db, settings, "curator", provider) == "complete"
    assert get_curated_memory_state(db, "c", "s")[1] == old
    assert run(db, settings) == "complete"
    before = db.execute(
        "SELECT covered_id,rewrite_identity,purge_epoch,source_floor_id,draft_json FROM memory_layer_state "
        "WHERE layer='curator'"
    ).fetchone()
    memory.purge_hindsight_session(db, "c", "s", app_settings=settings)
    after_purge = db.execute(
        "SELECT covered_id,rewrite_identity,purge_epoch,source_floor_id,draft_json FROM memory_layer_state "
        "WHERE layer='curator'"
    ).fetchone()
    new = append(db, "NEW")
    supplied.clear()
    assert run(db, settings, "curator", provider) == "complete"
    items, covered = get_curated_memory_state(db, "c", "s")
    assert {item["text"] for item in items} == {"OLD", "NEW"} and covered == new
    assert after_purge == before
    assert [source for _, source in supplied] == ["NEW"]
    assert supplied[0][0]["memories"][0]["text"] == "OLD"
    archive.retained.clear()
    assert run(db, settings) == "complete"
    assert [item["content"] for item in archive.retained] == ["NEW"]


@pytest.mark.parametrize("route", ["worker", "manual"])
def test_public_purge_revokes_curator_owner_without_erasing_native_progress(runtime, route):
    from bridge.memory_curator import curate_memory_now

    settings, db, _ = runtime
    append(db, "OLD")
    supplied = []
    assert run(db, settings, "curator", curator_provider(supplied)) == "complete"
    append(db, "NEW")

    def purge_and_replace():
        memory.purge_hindsight_session(db, "c", "s", app_settings=settings)
        fresh = claim_jobs(db, layers=("curator",))[0]
        assert fresh.token
        assert db.execute(
            "SELECT dirty_version>completed_version FROM memory_jobs WHERE layer='curator'"
        ).fetchone() == (1,)
        db.execute("UPDATE memory_jobs SET lease_token='replacement' WHERE layer='curator'")
        db.commit()

    provider = curator_provider(supplied, purge_and_replace)
    if route == "worker":
        assert run(db, settings, "curator", provider) == "stale_source"
    else:
        curate_memory_now(db, "", "c", SESSION, "Bob", provider_port=provider, app_settings=settings)
    assert {item["text"] for item in get_curated_memory_state(db, "c", "s")[0]} == {"OLD"}
    assert db.execute("SELECT lease_token FROM memory_jobs WHERE layer='curator'").fetchone() == ("replacement",)


@pytest.mark.parametrize("initial", ["Greeting", ""])
def test_valid_empty_scene_advances_before_later_meaningful_source(runtime, initial):
    settings, db, _ = runtime
    first = append(db, initial)
    supplied = []

    def generate(api_key, model, messages, **kwargs):
        source = messages[-1]["content"].split("Canonical source part:\n", 1)[1]
        supplied.append(source)
        if source == "At the harbor":
            return json.dumps(
                {
                    "state": {"location": "Harbor"},
                    "blocks": [{"text": "Harbor", "visibility": "shared", "known_by": []}],
                }
            )
        return '{"state":{},"blocks":[]}'

    provider = make_test_provider_port(generate_backend=generate)
    assert run(db, settings, "scene", provider) == "complete"
    assert get_scene_state(db, "c", "s") == ({}, first)
    assert scene_state_text(db, "c", "s") == ""
    scope = resolve_memory_scope(db, "c", SESSION, FIELDS)
    assert read_scene_block(db, scope).text == ""
    later = append(db, "At the harbor")
    assert run(db, settings, "scene", provider) == "complete"
    assert supplied == [initial, "At the harbor"]
    assert get_scene_state(db, "c", "s") == ({"location": "Harbor"}, later)
    scope = resolve_memory_scope(db, "c", SESSION, FIELDS)
    assert read_scene_block(db, scope).text == "Harbor"


@pytest.mark.parametrize(
    "payload",
    [
        {"blocks": []},
        {"state": None, "blocks": []},
        {"state": [], "blocks": []},
        {"state": {"unknown": "not scene"}, "blocks": []},
        {"state": {}},
        {"state": {}, "blocks": [{"text": "secret", "visibility": "invalid", "known_by": []}]},
    ],
)
def test_malformed_empty_scene_never_accepts_coverage(runtime, payload):
    settings, db, _ = runtime
    append(db, "Greeting")
    provider = make_test_provider_port(generate_backend=lambda *a, **k: json.dumps(payload))
    assert run(db, settings, "scene", provider) == "work_failed"
    assert get_scene_state(db, "c", "s") == ({}, 0)
    assert db.execute("SELECT covered_id FROM memory_layer_state WHERE layer='scene'").fetchone() == (0,)


def test_complete_scene_snapshot_preserves_no_change_and_accepts_explicit_clear(runtime):
    settings, db, _ = runtime

    def generate(api_key, model, messages, **kwargs):
        user = messages[-1]["content"]
        source = user.split("Canonical source part:\n", 1)[1]
        if source == "No change":
            return user.split("Previous classified scene:\n", 1)[1].split("\nSource role:", 1)[0]
        if source == "Clear established scene":
            return '{"state":{},"blocks":[]}'
        return '{"state":{"location":"Harbor"},"blocks":[{"text":"Harbor","visibility":"shared","known_by":[]}]}'

    provider = make_test_provider_port(generate_backend=generate)
    append(db, "At the harbor")
    assert run(db, settings, "scene", provider) == "complete"
    second = append(db, "No change")
    assert run(db, settings, "scene", provider) == "complete"
    assert get_scene_state(db, "c", "s") == ({"location": "Harbor"}, second)
    third = append(db, "Clear established scene")
    assert run(db, settings, "scene", provider) == "complete"
    assert get_scene_state(db, "c", "s") == ({}, third)
    assert scene_state_text(db, "c", "s") == ""


@pytest.mark.parametrize("route", ["worker", "seed"])
def test_raw_mapping_failure_rolls_back_source_acceptance_and_retries(runtime, route):
    settings, db, archive = runtime
    row = append(db, "Current source")
    db.execute(
        "CREATE TRIGGER reject_raw_mapping BEFORE INSERT ON hindsight_documents "
        "BEGIN SELECT RAISE(ABORT,'synthetic mapping failure'); END"
    )
    db.commit()
    assert seed_or_worker(route, db, settings) == ("degraded" if route == "seed" else "work_failed")
    document_id = archive.retained[0]["document_id"]
    assert document_id in archive.remote
    assert load_source(db, document_id) is None
    assert db.execute("SELECT covered_id FROM memory_layer_state WHERE layer='hindsight'").fetchone() == (0,)
    assert next_source_segment(db, "c", "s", "hindsight").document_id == document_id
    db.execute("DROP TRIGGER reject_raw_mapping")
    db.commit()
    assert seed_or_worker(route, db, settings) == ("ready" if route == "seed" else "complete")
    assert archive.retained[-1]["document_id"] == document_id
    assert db.execute("SELECT covered_id FROM memory_layer_state WHERE layer='hindsight'").fetchone() == (row,)
