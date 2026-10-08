"""Historical raw retirement and current classified extraction boundaries."""

import json
import sqlite3
from types import SimpleNamespace

import pytest
from application_test_setup import make_test_provider_port
from memory_runtime_test_support import isolated_memory_runtime as isolated_memory_runtime
from settings_test_support import make_test_settings

from bridge import memory, memory_backend
from bridge.memory_artifact_store import read_scene_block
from bridge.memory_contracts import MemoryFact
from bridge.memory_curator import get_curated_memory_state
from bridge.memory_fact_store import accept_source_facts, load_source, remember_local_fact
from bridge.memory_scope_store import resolve_memory_scope
from bridge.memory_store import (
    begin_archival_attempt,
    claim_jobs,
    finish_archival_attempt,
    next_source_segment,
    purge_external_memory,
    reconcile_archival_attempts,
    reserve_archival_source,
)
from bridge.memory_workers import run_memory_claim
from bridge.migrations import run_migrations
from bridge.scene_state import get_scene_state, scene_state_text
from bridge.schema import SCHEMA_MIGRATIONS, initialize_database_schema
from bridge.sqlite_store import db_connect, write_transaction

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
        assert "native-fact" in kwargs["tags"], "Production must never dispatch raw/conversation memory"
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
            items.append({"id": document_id, "tags": payload.get("tags", [])})
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


@pytest.fixture
def legacy_runtime(tmp_path, monkeypatch, isolated_memory_runtime):
    """A real pre-28 database containing captured historical request state."""
    settings = make_test_settings(home=tmp_path, db_file=tmp_path / "legacy.sqlite")
    archive = Archive()
    monkeypatch.setattr(memory_backend, "hindsight_client", lambda **kwargs: archive)
    db = sqlite3.connect(settings.db_file)
    run_migrations(db, tuple(m for m in SCHEMA_MIGRATIONS if m.version <= 27))
    db.execute(
        "INSERT INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,"
        "world_file,created_at,updated_at) VALUES('c','s','Story','','m','','',1,1)"
    )
    db.commit()
    yield settings, db, archive
    db.close()


def capture_historical_raw(db):
    """Capture a pre-upgrade request, without sending through the production client."""
    source = next_source_segment(db, "c", "s", "hindsight")
    assert source is not None and reserve_archival_source(db, source)
    with write_transaction(db):
        token = begin_archival_attempt(db, source)
    payload = {
        "document_id": source.document_id,
        "content": source.content,
        "tags": ["session:s"],
        "bank_id": memory_backend.hindsight_bank_id("c"),
        "historical_fixture": True,
    }
    return source, token, payload


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


@pytest.mark.parametrize("known_completion", [False, True])
@pytest.mark.parametrize("change", ["rewrite", "delete", "recreate", "purge"])
def test_historical_raw_late_completion_is_retired_without_stale_mapping(legacy_runtime, change, known_completion):
    settings, db, archive = legacy_runtime
    row = append(db, "Old canonical source")
    source, token, payload = capture_historical_raw(db)
    initialize_database_schema(db)
    change_source(db, row, change)
    archive.remote[source.document_id] = payload  # Synthetic historical upstream completion.
    if known_completion:
        finish_archival_attempt(db, token)
    reconcile_archival_attempts(db)
    cleanup(db, settings)
    assert source.document_id not in archive.remote
    assert archive.retained == []
    assert load_source(db, source.document_id) is None
    assert db.execute("SELECT 1 FROM hindsight_documents WHERE document_id=?", (source.document_id,)).fetchone() is None
    assert db.execute("SELECT finished FROM memory_archival_attempts WHERE attempt_token=?", (token,)).fetchone() == (
        None if known_completion else (0,)
    )
    if change == "recreate":
        remember_local_fact(db, "c", "s", "Bob", "Current incarnation fact.")
        assert run(db, settings) == "complete"
        new_id = archive.retained[-1]["document_id"]
        assert new_id != source.document_id and memory_backend._retirement_would_delete_current_source(db, new_id)


def test_historical_completion_reopens_after_successful_delete(legacy_runtime):
    settings, db, archive = legacy_runtime
    append(db, "Delayed historical source")
    source, token, payload = capture_historical_raw(db)
    initialize_database_schema(db)
    cleanup(db, settings)
    assert db.execute("SELECT deleted FROM memory_retired_documents").fetchone() == (0,)
    archive.remote[source.document_id] = payload
    finish_archival_attempt(db, token)
    reconcile_archival_attempts(db)
    assert db.execute("SELECT deleted,next_attempt_at FROM memory_retired_documents").fetchone() == (0, 0)
    cleanup(db, settings)
    assert source.document_id not in archive.remote
    assert db.execute("SELECT deleted FROM memory_retired_documents").fetchone() == (1,)
    assert archive.retained == []


def test_pending_historical_attempt_is_retired_after_restart_without_resending(legacy_runtime, monkeypatch):
    settings, db, archive = legacy_runtime
    append(db, "Uncertain historical request")
    source, token, payload = capture_historical_raw(db)
    initialize_database_schema(db)
    cleanup(db, settings)
    archive.remote[source.document_id] = payload
    later = memory_backend.time.time() + 1000
    monkeypatch.setattr(memory_backend.time, "time", lambda: later)
    reopened = db_connect(app_settings=settings)
    try:
        cleanup(reopened, settings)
        assert source.document_id not in archive.remote
        assert load_source(reopened, source.document_id) is None
        assert reopened.execute(
            "SELECT finished FROM memory_archival_attempts WHERE attempt_token=?", (token,)
        ).fetchone() == (0,)
        assert reopened.execute("SELECT covered_id FROM memory_layer_state WHERE layer='hindsight'").fetchone() == (0,)
        assert claim_jobs(reopened, layers=("hindsight",)) == []
        assert archive.retained == []
    finally:
        reopened.close()


def test_pending_native_prefix_reuses_identity_after_unrelated_suffix_rewrite(runtime):
    settings, db, archive = runtime
    append(db, "Prefix")
    accept_source_facts(
        db,
        next_source_segment(db, "c", "s", "episodes"),
        [MemoryFact("fact", 0.9, "Accepted prefix summary.", "shared", ())],
    )
    suffix = append(db, "Suffix")

    def fail(kwargs):
        raise RuntimeError("Synthetic retain outage")

    archive.on_retain = fail
    assert run(db, settings) == "retain_failed"
    old_id = archive.retained[0]["document_id"]
    db.execute("UPDATE messages SET content='Replacement suffix' WHERE id=?", (suffix,))
    db.commit()
    archive.on_retain = None
    assert run(db, settings) == "complete"
    assert archive.retained[1]["document_id"] == old_id
    assert [item["content"] for item in archive.retained] == ["Accepted prefix summary."] * 2
    assert db.execute("SELECT count(*) FROM memory_segments WHERE layer='hindsight'").fetchone() == (0,)


@pytest.mark.parametrize("change", ["lease", "mode"])
def test_native_interruption_keeps_current_index_and_replacement_ownership(runtime, change):
    settings, db, archive = runtime
    append(db, "Current source")
    remember_local_fact(db, "c", "s", "Bob", "Current local fact.")

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
    assert db.execute("SELECT state FROM memory_fact_index WHERE document_id=?", (document_id,)).fetchone() == (
        "pending",
    )
    assert db.execute("SELECT 1 FROM memory_retired_documents WHERE document_id=?", (document_id,)).fetchone() is None
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
    assert archive.retained == []  # Curator drafts are local; only classified accepted facts are indexed.


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
    expected = "invalid_audience" if payload.get("blocks") else "invalid_shape"
    assert run(db, settings, "scene", provider) == expected
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


def test_native_mapping_failure_rolls_back_acceptance_and_retries_same_id(runtime):
    settings, db, archive = runtime
    append(db, "Current source")
    remember_local_fact(db, "c", "s", "Bob", "Current native fact.")
    db.execute(
        "CREATE TRIGGER reject_mapping BEFORE INSERT ON hindsight_documents "
        "BEGIN SELECT RAISE(ABORT,'synthetic mapping failure'); END"
    )
    db.commit()
    assert run(db, settings) == "work_failed"
    document_id = archive.retained[0]["document_id"]
    assert document_id in archive.remote
    assert db.execute("SELECT state FROM memory_fact_index").fetchone() == ("pending",)
    assert db.execute("SELECT count(*) FROM hindsight_documents").fetchone() == (0,)
    db.execute("DROP TRIGGER reject_mapping")
    db.commit()
    assert run(db, settings) == "complete"
    assert archive.retained[-1]["document_id"] == document_id
    assert db.execute("SELECT state FROM memory_fact_index").fetchone() == ("retained",)


def test_historical_failed_reopening_preserves_finished_token_until_restart(legacy_runtime):
    settings, db, archive = legacy_runtime
    append(db, "Old source with a delayed remote completion")
    source, token, payload = capture_historical_raw(db)
    initialize_database_schema(db)
    cleanup(db, settings)
    archive.remote[source.document_id] = payload
    finish_archival_attempt(db, token)
    db.execute(
        "CREATE TRIGGER reject_reopening BEFORE UPDATE ON memory_retired_documents "
        "BEGIN SELECT RAISE(ABORT,'synthetic stale reopening failure'); END"
    )
    db.commit()
    with pytest.raises(sqlite3.IntegrityError, match="reopening"):
        reconcile_archival_attempts(db)
    assert db.execute("SELECT finished FROM memory_archival_attempts WHERE attempt_token=?", (token,)).fetchone() == (
        1,
    )
    db.execute("DROP TRIGGER reject_reopening")
    db.commit()
    reopened = db_connect(app_settings=settings)
    try:
        cleanup(reopened, settings)
        assert source.document_id not in archive.remote
        assert (
            reopened.execute("SELECT 1 FROM memory_archival_attempts WHERE attempt_token=?", (token,)).fetchone()
            is None
        )
        assert archive.retained == []
    finally:
        reopened.close()
