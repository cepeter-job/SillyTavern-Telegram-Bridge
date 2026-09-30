import sqlite3
import time

from bridge.npc_repository import insert_npc_entity
from bridge.npc_service import NpcService
from bridge.npc_types import NpcExtractionGroup, NpcOperation
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


def _op(field, value, *, mode="mutable", visibility="shared", known_by=()):
    return NpcOperation(field, "set", value, mode, visibility, tuple(known_by))


def _apply(service, db, name, source_rowid, *operations, aliases=()):
    return service.apply_group(
        db,
        "chat",
        "s1",
        NpcExtractionGroup(name, tuple(aliases), tuple(operations)),
        source_rowid=source_rowid,
        primary_name="Alice",
        user_name="User",
    )


def _context(service, db, query, history=(), *, active="Alice", through_rowid=None):
    return service.context_for_prompt(
        db,
        "chat",
        {"session_id": "s1"},
        {"name": active},
        query,
        list(history),
        through_rowid=through_rowid,
    )


def test_exact_name_and_alias_mentions_rank_the_same_npc():
    db = _db()
    service = NpcService()
    try:
        _apply(service, db, "Rowan", 20, _op("role", "Guard"))
        _apply(service, db, "Maya Torres", 10, _op("role", "Archivist"), aliases=("Maya",))

        by_name = _context(service, db, "Ask Maya Torres about the ledger.")
        by_alias = _context(service, db, "Ask Maya about the ledger.")

        assert by_name.index("NPC: Maya Torres") < by_name.index("NPC: Rowan")
        assert by_alias.index("NPC: Maya Torres") < by_alias.index("NPC: Rowan")
    finally:
        db.close()


def test_scene_participant_outranks_more_recent_fallback():
    db = _db()
    service = NpcService()
    try:
        _apply(service, db, "Maya Torres", 10, _op("role", "Archivist"))
        _apply(service, db, "Rowan", 30, _op("role", "Guard"))
        db.execute(
            """
            INSERT INTO scene_states(chat_id,session_id,state_json,updated_through_rowid,updated_at)
            VALUES(?,?,?,?,?)
            """,
            ("chat", "s1", '{"participants":["Maya Torres"]}', 30, time.time()),
        )
        db.commit()

        context = _context(service, db, "Continue.")

        assert context.index("NPC: Maya Torres") < context.index("NPC: Rowan")
    finally:
        db.close()


def test_recent_history_mention_outranks_recency_fallback():
    db = _db()
    service = NpcService()
    try:
        _apply(service, db, "Maya Torres", 10, _op("role", "Archivist"))
        _apply(service, db, "Rowan", 30, _op("role", "Guard"))

        context = _context(
            service,
            db,
            "Continue.",
            history=(("assistant", "Maya Torres left the archive."),),
        )

        assert context.index("NPC: Maya Torres") < context.index("NPC: Rowan")
    finally:
        db.close()


def test_context_enforces_npc_count_and_character_budget():
    db = _db()
    service = NpcService()
    try:
        for index in range(6):
            _apply(
                service,
                db,
                f"NPC {index}",
                10 + index,
                _op("background", "x" * 1900, mode="fixed"),
                _op("role", f"Role {index}"),
            )

        context = _context(service, db, "Continue.")

        assert context.count("NPC: ") <= 4
        assert len(context) <= 6000
    finally:
        db.close()


def test_restricted_fields_are_visible_only_to_explicit_knower():
    db = _db()
    service = NpcService()
    try:
        _apply(
            service,
            db,
            "Maya Torres",
            10,
            _op("role", "Archivist"),
            NpcOperation("secrets", "append", "Vault code is 731", "mutable", "restricted", ("Alice",)),
        )

        alice = _context(service, db, "Maya Torres", active="Alice")
        bob = _context(service, db, "Maya Torres", active="Bob")

        assert "Vault code is 731" in alice
        assert "Vault code is 731" not in bob
        assert "Role: Archivist" in bob
    finally:
        db.close()


def test_npc_with_only_hidden_fields_emits_no_empty_shell():
    db = _db()
    service = NpcService()
    try:
        _apply(
            service,
            db,
            "Maya Torres",
            10,
            NpcOperation("secrets", "append", "Vault code is 731", "mutable", "restricted", ("Alice",)),
        )

        context = _context(service, db, "Maya Torres", active="Bob")

        assert "Maya Torres" not in context
    finally:
        db.close()


def test_historical_context_restores_pre_edit_state_and_excludes_future_secret():
    db = _db()
    service = NpcService()
    try:
        _apply(service, db, "Maya Torres", 100, _op("relationship", "cautious"))
        _apply(service, db, "Maya Torres", 150, _op("relationship", "hostile"))
        _apply(
            service,
            db,
            "Maya Torres",
            180,
            NpcOperation("secrets", "append", "Knows the vault code", "mutable", "restricted", ("Alice",)),
        )

        context = _context(service, db, "Maya Torres", through_rowid=140)

        assert "Relationship: cautious" in context
        assert "hostile" not in context
        assert "vault code" not in context
    finally:
        db.close()


def test_future_scene_state_is_ignored_for_historical_context():
    db = _db()
    service = NpcService()
    try:
        _apply(service, db, "Maya Torres", 10, _op("role", "Archivist"))
        _apply(service, db, "Rowan", 30, _op("role", "Guard"))
        db.execute(
            """
            INSERT INTO scene_states(chat_id,session_id,state_json,updated_through_rowid,updated_at)
            VALUES(?,?,?,?,?)
            """,
            ("chat", "s1", '{"participants":["Maya Torres"]}', 200, time.time()),
        )
        db.commit()

        context = _context(service, db, "Continue.", through_rowid=140)

        assert context.index("NPC: Rowan") < context.index("NPC: Maya Torres")
    finally:
        db.close()


def test_ambiguous_alias_query_does_not_render_either_candidate():
    db = _db()
    service = NpcService()
    try:
        with write_transaction(db):
            first = insert_npc_entity(db, "chat", "s1", "maya torres", "Maya Torres", ("May",), 10, 1.0)
            second = insert_npc_entity(db, "chat", "s1", "maya chen", "Maya Chen", ("May",), 20, 2.0)
            db.execute(
                """
                INSERT INTO npc_fields(
                    npc_id,field_key,value_json,field_mode,visibility,known_by_json,updated_rowid,updated_at
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (first, "role", '"Archivist"', "mutable", "shared", "[]", 10, 1.0),
            )
            db.execute(
                """
                INSERT INTO npc_fields(
                    npc_id,field_key,value_json,field_mode,visibility,known_by_json,updated_rowid,updated_at
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (second, "role", '"Merchant"', "mutable", "shared", "[]", 20, 2.0),
            )

        context = _context(service, db, "Ask May.")

        assert "Maya Torres" not in context
        assert "Maya Chen" not in context
    finally:
        db.close()
