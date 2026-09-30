import json
import sqlite3
import time

from application_test_setup import make_test_provider_port
from settings_test_support import SettingsBuilder

from bridge.application_composition import initialize_extensions
from bridge.extension_registry import extension_registry_snapshot
from bridge.npc_extraction import parse_npc_extraction, refresh_npc_state_now
from bridge.npc_repository import (
    find_npc_exact,
    get_npc_extraction_coverage,
    set_npc_extraction_coverage,
)
from bridge.schema import initialize_database_schema
from bridge.sqlite_store import write_transaction


def _db():
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys=ON")
    initialize_database_schema(db)
    now = time.time()
    db.execute(
        """
        INSERT INTO sessions(
            chat_id,session_id,title,character_file,model_id,persona_id,world_file,
            author_note,system_prompt,response_language,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        ("chat", "s1", "s1", "char.png", "p::m", "", "", "", "", "auto", now, now),
    )
    db.commit()
    return db


def _messages(db, texts=("hello", "Maya says she is the archivist.")):
    now = time.time()
    ids = []
    for index, text in enumerate(texts):
        cursor = db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", "s1", "user" if index % 2 == 0 else "assistant", text, now + index),
        )
        ids.append(int(cursor.lastrowid))
    db.commit()
    return ids


def _session():
    return {"session_id": "s1", "model_id": "p::m", "persona_id": ""}


def _fields():
    return {"name": "Alice"}


def _payload(
    name="Maya Torres",
    *,
    field="role",
    value="Archivist",
    mode="mutable",
    visibility="shared",
    known_by=(),
):
    return json.dumps(
        {
            "npcs": [
                {
                    "name": name,
                    "aliases": [],
                    "operations": [
                        {
                            "field": field,
                            "op": "set",
                            "value": value,
                            "mode": mode,
                            "visibility": visibility,
                            "known_by": list(known_by),
                        }
                    ],
                }
            ]
        }
    )


def test_parser_accepts_valid_groups_and_rejects_invalid_identity_and_secret_scope():
    raw = """
    {"npcs":[
      {"name":"Maya Torres","aliases":["Maya"],"operations":[
        {"field":"role","op":"set","value":"Archivist","mode":"mutable","visibility":"shared","known_by":[]},
        {"field":"secrets","op":"append","value":"Vault code","mode":"mutable"}
      ]},
      {"name":"Alice","aliases":[],"operations":[
        {"field":"role","op":"set","value":"Hero","mode":"mutable","visibility":"shared","known_by":[]}
      ]},
      {"name":"User","aliases":[],"operations":[
        {"field":"role","op":"set","value":"Player","mode":"mutable","visibility":"shared","known_by":[]}
      ]}
    ]}
    """
    groups = parse_npc_extraction(raw, primary_name="Alice", user_name="User")
    assert len(groups) == 1
    assert groups[0].name == "Maya Torres"
    assert [op.field_key for op in groups[0].operations] == ["role"]


def test_parser_returns_empty_for_malformed_or_unsupported_payload():
    assert parse_npc_extraction("not-json", primary_name="Alice", user_name="User") == []
    raw = _payload(name="Maya", field="unsupported", value="x")
    assert parse_npc_extraction(raw, primary_name="Alice", user_name="User") == []


def test_refresh_extracts_once_and_advances_coverage():
    db = _db()
    _messages(db)
    calls = []

    def generate(_key, model, messages, **kwargs):
        calls.append((model, messages, kwargs.get("session_id")))
        payload = json.loads(_payload())
        payload["npcs"][0]["aliases"] = ["Maya"]
        return json.dumps(payload)

    try:
        provider = make_test_provider_port(generate_backend=generate)
        applied = refresh_npc_state_now(
            db,
            "chat",
            _session(),
            _fields(),
            provider_port=provider,
            app_settings=SettingsBuilder().build(),
        )
        coverage = get_npc_extraction_coverage(db, "chat", "s1")
        assert applied == 1
        assert len(calls) == 1
        assert calls[0][2] == "npc-state:chat:s1"
        assert find_npc_exact(db, "chat", "s1", "maya torres") is not None
        assert coverage > 0

        again = refresh_npc_state_now(
            db,
            "chat",
            _session(),
            _fields(),
            provider_port=provider,
            app_settings=SettingsBuilder().build(),
        )
        assert again == 0
        assert len(calls) == 1
    finally:
        db.close()


def test_provider_failure_leaves_coverage_unchanged():
    db = _db()
    _messages(db)

    def generate(*_args, **_kwargs):
        raise RuntimeError("provider down")

    try:
        applied = refresh_npc_state_now(
            db,
            "chat",
            _session(),
            _fields(),
            provider_port=make_test_provider_port(generate_backend=generate),
            app_settings=SettingsBuilder().build(),
        )
        assert applied == 0
        assert get_npc_extraction_coverage(db, "chat", "s1") == 0
    finally:
        db.close()


def test_stale_worker_is_rejected_when_coverage_rewinds_during_generation():
    db = _db()
    ids = _messages(db, ("old", "old reply", "new", "new reply"))
    with write_transaction(db):
        set_npc_extraction_coverage(db, "chat", "s1", ids[1], 1.0)

    def generate(*_args, **_kwargs):
        with write_transaction(db):
            set_npc_extraction_coverage(db, "chat", "s1", max(0, ids[1] - 1), 2.0)
        return _payload(name="Maya")

    try:
        applied = refresh_npc_state_now(
            db,
            "chat",
            _session(),
            _fields(),
            provider_port=make_test_provider_port(generate_backend=generate),
            app_settings=SettingsBuilder().build(),
        )
        assert applied == 0
        assert find_npc_exact(db, "chat", "s1", "maya") is None
        assert get_npc_extraction_coverage(db, "chat", "s1") == ids[1] - 1
    finally:
        db.close()


def test_deleted_session_during_generation_prevents_write():
    db = _db()
    _messages(db)

    def generate(*_args, **_kwargs):
        db.execute("DELETE FROM sessions WHERE chat_id='chat' AND session_id='s1'")
        db.commit()
        return _payload(name="Maya")

    try:
        applied = refresh_npc_state_now(
            db,
            "chat",
            _session(),
            _fields(),
            provider_port=make_test_provider_port(generate_backend=generate),
            app_settings=SettingsBuilder().build(),
        )
        assert applied == 0
        assert db.execute("SELECT COUNT(*) FROM npc_entities").fetchone()[0] == 0
    finally:
        db.close()


def test_application_composition_registers_npc_post_retain_hook_once():
    initialize_extensions()
    first = extension_registry_snapshot()
    initialize_extensions()
    second = extension_registry_snapshot()
    assert first == second
    assert "npc_state" in first["post_retain"]
