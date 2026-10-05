"""Story knowledge is authorized by local temporal attestations, never remote text."""

import sqlite3
from types import SimpleNamespace

import pytest

from bridge import memory_backend
from bridge.episodic_memory import episodic_context_for_prompt, store_episodic_memory
from bridge.memory_store import next_source_segment, purge_external_memory
from bridge.schema import initialize_database_schema
from bridge.sqlite_store import write_transaction


@pytest.fixture(autouse=True)
def forbid_real_hindsight_client(monkeypatch):
    def forbidden(**kwargs):
        pytest.fail("Story memory tests must explicitly fake the Hindsight client")

    monkeypatch.setattr(memory_backend, "hindsight_client", forbidden)


@pytest.fixture
def db():
    connection = sqlite3.connect(":memory:")
    initialize_database_schema(connection)
    for sid, created in [("s", 1.0), ("other", 2.0)]:
        connection.execute(
            "INSERT INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,"
            "world_file,created_at,updated_at) VALUES('c',?,'Story','','m','','',?,?)",
            (sid, created, created),
        )
    connection.commit()
    yield connection
    connection.close()


def append(db, text="The silver key is hidden in the tower.", sid="s"):
    row = db.execute(
        "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('c',?,'user',?,3)",
        (sid, text),
    )
    db.commit()
    return int(row.lastrowid)


def scope(db, name="Mira", through=None, **kwargs):
    from bridge.memory_scope_store import resolve_memory_scope

    return resolve_memory_scope(db, "c", {"session_id": "s"}, {"name": name}, through_rowid=through, **kwargs)


def accept(db, text="The silver key is hidden in the tower.", audience=("Mira",), visibility="restricted"):
    from bridge.memory_contracts import MemoryFact
    from bridge.memory_fact_store import accept_source_facts

    source = next_source_segment(db, "c", "s", "episodes")
    assert source is not None
    ids = accept_source_facts(db, source, [MemoryFact("fact", 0.9, text, visibility, audience)])
    assert ids is not None
    return source, ids[0]


def read(db, read_scope, query="silver key"):
    from bridge.memory_scope_store import read_episodic_block

    assert read_scope is not None
    return read_episodic_block(db, read_scope, query)


def test_legacy_unknown_provenance_is_not_character_authority(db):
    row = append(db)
    with write_transaction(db):
        store_episodic_memory(
            db,
            "c",
            "s",
            kind="fact",
            importance=0.9,
            summary="The silver key is hidden.",
            source_start_rowid=row,
            source_end_rowid=row,
            visibility="restricted",
            known_by=("Mira",),
        )
    assert episodic_context_for_prompt(db, "c", {"session_id": "s"}, {"name": "Mira"}, "silver key") == ""
    assert db.execute("SELECT count(*) FROM episodic_memories").fetchone()[0] == 1


def test_audience_attestations_are_additive_temporal_and_retry_idempotent(db):
    from bridge.memory_contracts import MemoryFact
    from bridge.memory_fact_store import accept_source_facts

    first = append(db)
    source, memory_id = accept(db)
    assert accept_source_facts(
        db, source, [MemoryFact("fact", 0.9, "The silver key is hidden in the tower.", "restricted", ("Mira",))]
    ) == (memory_id,)
    second = append(db, "Bob learns where the silver key is hidden.")
    _, later_id = accept(db, audience=("Bob",))
    assert later_id != memory_id
    assert read(db, scope(db, "Bob", first)).text == ""
    assert "silver key" in read(db, scope(db, "Bob", second)).text
    db.execute("UPDATE messages SET content='Bob never learned the secret.' WHERE id=?", (second,))
    db.commit()
    assert read(db, scope(db, "Bob")).text == ""
    assert "silver key" in read(db, scope(db, "Mira")).text


def test_source_prefix_survives_append_and_unrelated_rewrite(db):
    from bridge.memory_scope_store import validate_memory_blocks

    first = append(db)
    accept(db)
    captured = scope(db, through=first)
    block = read(db, captured)
    later = append(db, "The weather changes.")
    db.execute("UPDATE messages SET content='It rains.' WHERE id=?", (later,))
    db.commit()
    assert validate_memory_blocks(db, captured, (block,))[0].text == block.text
    db.execute("UPDATE messages SET content='No silver key exists.' WHERE id=?", (first,))
    db.commit()
    assert validate_memory_blocks(db, captured, (block,))[0].text == ""


def test_empty_success_and_transaction_failure_keep_source_fact_index_atomic(db):
    from bridge.memory_contracts import MemoryFact
    from bridge.memory_fact_store import accept_source_facts

    append(db)
    source = next_source_segment(db, "c", "s", "episodes")
    db.execute(
        "CREATE TRIGGER fail_fact_index BEFORE INSERT ON memory_fact_index "
        "BEGIN SELECT RAISE(ABORT,'synthetic failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        accept_source_facts(db, source, [MemoryFact("fact", 0.9, "silver key", "shared", ())])
    assert db.execute("SELECT count(*) FROM memory_segments").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM episodic_memories").fetchone()[0] == 0
    db.execute("DROP TRIGGER fail_fact_index")
    db.commit()
    assert accept_source_facts(db, source, []) == ()
    assert next_source_segment(db, "c", "s", "episodes") is None


def test_explicit_fact_is_immediate_character_scoped_and_equal_anchor_historical_denied(db):
    from bridge.memory_fact_store import remember_local_fact

    anchor = append(db, "They reach the tower.")
    first = remember_local_fact(db, "c", "s", "Mira", "The silver key is in my pocket.")
    assert first == remember_local_fact(db, "c", "s", "Mira", "The silver key is in my pocket.")
    assert "pocket" in read(db, scope(db)).text
    assert read(db, scope(db, "Bob")).text == ""
    assert read(db, scope(db, through=anchor)).text == ""
    assert "pocket" in read(db, scope(db, through=append(db, "A new turn."))).text
    with pytest.raises(ValueError, match="character"):
        remember_local_fact(db, "c", "s", " ", "No global escape")


def test_explicit_watermark_and_purge_epoch_do_not_retroactively_grant(db):
    from bridge.memory_fact_store import remember_local_fact

    append(db)
    captured = scope(db)
    first = remember_local_fact(db, "c", "s", "Mira", "The silver key is in my pocket.")
    assert read(db, captured).text == ""
    purge_external_memory(db, "c", "s", purge_epoch=1)
    assert "pocket" in read(db, scope(db)).text
    assert db.execute("SELECT state FROM memory_fact_index").fetchone()[0] == "retired"
    second = remember_local_fact(db, "c", "s", "Mira", "The silver key is in my pocket.")
    assert first != second
    assert db.execute("SELECT external_epoch,state FROM memory_fact_index WHERE memory_id=?", (second,)).fetchone() == (
        1,
        "pending",
    )


def test_external_purge_does_not_reindex_pre_purge_native_source(db):
    append(db)
    purge_external_memory(db, "c", "s", purge_epoch=1)
    accept(db)
    assert "silver key" in read(db, scope(db)).text
    assert db.execute("SELECT count(*) FROM memory_fact_index").fetchone()[0] == 0
    append(db, "A new public fact about the silver key.")
    accept(db, audience=(), visibility="shared")
    assert db.execute("SELECT external_epoch,state FROM memory_fact_index").fetchall() == [(1, "pending")]


def test_ensemble_scope_requires_all_readers_and_no_implicit_narrator(db):
    append(db)
    accept(db)
    assert read(db, scope(db, principals=("Mira", "Bob"))).text == ""
    assert read(db, scope(db, "")).text == ""
    assert "silver key" in read(db, scope(db, "", consumer="narrator")).text


def test_late_recall_uses_local_text_and_drops_rewritten_or_foreign_candidates(db, monkeypatch):
    from bridge.memory_backend import recall_scoped_memory
    from bridge.memory_scope_store import validate_memory_blocks

    row = append(db)
    _, memory_id = accept(db, audience=(), visibility="shared")
    document = db.execute("SELECT document_id FROM memory_fact_index WHERE memory_id=?", (memory_id,)).fetchone()[0]
    db.execute("UPDATE memory_fact_index SET state='retained'")
    db.commit()
    captured = scope(db, "Bob")
    monkeypatch.setattr(
        memory_backend,
        "recall_memory_results",
        lambda *a, **k: [
            SimpleNamespace(document_id=document, text="MALICIOUS PRIVATE ENRICHMENT"),
            SimpleNamespace(document_id="unmapped-raw-transcript", text="Mira's private secret"),
        ],
    )
    block = recall_scoped_memory(db, captured, "silver key", app_settings=SimpleNamespace())
    assert "silver key" in block.text
    assert "MALICIOUS" not in block.text and "private secret" not in block.text
    db.execute("UPDATE messages SET content='Changed.' WHERE id=?", (row,))
    db.commit()
    assert validate_memory_blocks(db, captured, (block,))[0].text == ""


def test_session_recreation_and_explicit_prefix_rewrite_drop_old_evidence(db):
    from bridge.memory_fact_store import remember_local_fact
    from bridge.memory_scope_store import validate_memory_blocks

    anchor = append(db)
    remember_local_fact(db, "c", "s", "Mira", "The silver key is old.")
    captured = scope(db)
    block = read(db, captured)
    db.execute("UPDATE messages SET content='Replacement.' WHERE id=?", (anchor,))
    db.commit()
    assert validate_memory_blocks(db, captured, (block,))[0].text == ""
    db.execute("DELETE FROM sessions WHERE chat_id='c' AND session_id='s'")
    db.execute(
        "INSERT INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,"
        "world_file,created_at,updated_at) VALUES('c','s','New','','m','','',99,99)"
    )
    db.commit()
    assert validate_memory_blocks(db, captured, (block,))[0].text == ""


def test_legacy_storage_also_preserves_additive_audiences_without_union(db):
    first = append(db)
    second = append(db, "Bob learns the same fact later.")
    with write_transaction(db):
        for row, reader in [(first, "Mira"), (second, "Bob")]:
            assert store_episodic_memory(
                db,
                "c",
                "s",
                kind="fact",
                importance=0.9,
                summary="A silver key fact",
                source_start_rowid=row,
                source_end_rowid=row,
                visibility="restricted",
                known_by=(reader,),
            )
    assert db.execute(
        "SELECT e.source_end_rowid,v.known_by_json FROM episodic_memories e "
        "JOIN episodic_memory_visibility v ON v.memory_id=e.memory_id ORDER BY e.memory_id"
    ).fetchall() == [(first, '["Mira"]'), (second, '["Bob"]')]


def test_identical_explicit_retry_does_not_dirty_finished_work_again(db):
    from bridge.memory_fact_store import remember_local_fact

    append(db)
    memory_id = remember_local_fact(db, "c", "s", "Mira", "A silver key")
    before = db.execute("SELECT dirty_version FROM memory_jobs WHERE layer='hindsight'").fetchone()
    assert remember_local_fact(db, "c", "s", "Mira", "A silver key") == memory_id
    assert db.execute("SELECT dirty_version FROM memory_jobs WHERE layer='hindsight'").fetchone() == before
