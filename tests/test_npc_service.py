import sqlite3
import time

import pytest
from persisted_state_test_support import find_test_npc

from bridge.npc_repository import list_npc_field_history, load_npc_fields
from bridge.npc_service import NpcService
from bridge.npc_types import NpcExtractionGroup, NpcOperation
from bridge.schema import initialize_database_schema


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


def _group(name="Maya Torres", operations=(), aliases=()):
    return NpcExtractionGroup(name=name, aliases=tuple(aliases), operations=tuple(operations))


def _op(field, value, *, operation="set", mode="mutable", visibility="shared", known_by=()):
    return NpcOperation(
        field_key=field,
        operation=operation,
        value=value,
        field_mode=mode,
        visibility=visibility,
        known_by=tuple(known_by),
    )


@pytest.mark.parametrize("operation,value", [("append", "Bob secret"), ("remove", "Alice secret")])
def test_npc_append_preserves_restricted_audience(operation, value):
    db = _db()
    service = NpcService()
    try:

        def apply(op, content, names, rowid):
            return service.apply_group(
                db,
                "chat",
                "s1",
                _group(operations=[_op("secrets", content, operation=op, visibility="restricted", known_by=names)]),
                source_rowid=rowid,
                primary_name="Alice",
                user_name="User",
            )

        assert apply("set", ["Alice secret"], ("Alice",), 1).applied == 1
        npc = find_test_npc(db, "chat", "s1", "maya torres")
        before = load_npc_fields(db, npc.npc_id)["secrets"]
        history = list_npc_field_history(db, npc.npc_id)
        result = apply(operation, value, ("Bob",), 2)
        assert result.applied == 0
        assert result.rejected == 1
        assert load_npc_fields(db, npc.npc_id)["secrets"] == before
        assert list_npc_field_history(db, npc.npc_id) == history
        assert apply("append", "Second Alice secret", (" alice ",), 3).applied == 1
        assert load_npc_fields(db, npc.npc_id)["secrets"].known_by == ("Alice",)
        assert apply("set", ["Bob secret"], ("Bob",), 4).applied == 1
        assert load_npc_fields(db, npc.npc_id)["secrets"].known_by == ("Bob",)
        assert apply("set", ["Bob secret"], ("Alice",), 5).applied == 1
        assert load_npc_fields(db, npc.npc_id)["secrets"].known_by == ("Alice",)
    finally:
        db.close()


def test_fixed_field_is_write_once_and_rejected_overwrite_creates_no_history():
    db = _db()
    service = NpcService()
    try:
        first = service.apply_group(
            db,
            "chat",
            "s1",
            _group(operations=[_op("voice", "soft alto", mode="fixed")]),
            source_rowid=10,
            primary_name="Alice",
            user_name="User",
        )
        second = service.apply_group(
            db,
            "chat",
            "s1",
            _group(operations=[_op("voice", "deep baritone", mode="fixed")]),
            source_rowid=20,
            primary_name="Alice",
            user_name="User",
        )
        npc = find_test_npc(db, "chat", "s1", "maya torres")
        fields = load_npc_fields(db, npc.npc_id)
        assert first.applied == 1
        assert second.applied == 0
        assert second.rejected == 1
        assert fields["voice"].value == "soft alto"
        assert len(list_npc_field_history(db, npc.npc_id)) == 1
    finally:
        db.close()


def test_mutable_set_records_full_before_after_history_and_noop_is_ignored():
    db = _db()
    service = NpcService()
    try:
        service.apply_group(
            db,
            "chat",
            "s1",
            _group(operations=[_op("agenda", "Find the ledger")]),
            source_rowid=10,
            primary_name="Alice",
            user_name="User",
        )
        service.apply_group(
            db,
            "chat",
            "s1",
            _group(operations=[_op("agenda", "Escape before dawn")]),
            source_rowid=20,
            primary_name="Alice",
            user_name="User",
        )
        noop = service.apply_group(
            db,
            "chat",
            "s1",
            _group(operations=[_op("agenda", "Escape before dawn")]),
            source_rowid=30,
            primary_name="Alice",
            user_name="User",
        )
        npc = find_test_npc(db, "chat", "s1", "maya torres")
        changes = list_npc_field_history(db, npc.npc_id)
        assert [change.before.value if change.before else None for change in changes] == [None, "Find the ledger"]
        assert [change.after.value if change.after else None for change in changes] == [
            "Find the ledger",
            "Escape before dawn",
        ]
        assert noop.applied == 0
        assert len(changes) == 2
    finally:
        db.close()


def test_list_append_deduplicates_and_remove_uses_normalized_exact_match():
    db = _db()
    service = NpcService()
    try:
        for rowid, operation, value in (
            (10, "append", "Forged the ledger"),
            (11, "append", "  forged   the LEDGER "),
            (12, "append", "Owes the guard a favor"),
            (13, "remove", "forged the ledger"),
        ):
            service.apply_group(
                db,
                "chat",
                "s1",
                _group(
                    operations=[
                        _op("secrets", value, operation=operation, visibility="restricted", known_by=("Maya Torres",))
                    ]
                ),
                source_rowid=rowid,
                primary_name="Alice",
                user_name="User",
            )
        npc = find_test_npc(db, "chat", "s1", "maya torres")
        fields = load_npc_fields(db, npc.npc_id)
        assert fields["secrets"].value == ["Owes the guard a favor"]
        assert len(list_npc_field_history(db, npc.npc_id)) == 3
    finally:
        db.close()


def test_restricted_requires_known_by_and_secret_fails_closed_when_shared():
    db = _db()
    service = NpcService()
    try:
        with pytest.raises(ValueError, match="known_by"):
            service.apply_group(
                db,
                "chat",
                "s1",
                _group(operations=[_op("agenda", "Hide", visibility="restricted")]),
                source_rowid=10,
                primary_name="Alice",
                user_name="User",
            )
        result = service.apply_group(
            db,
            "chat",
            "s1",
            _group(operations=[_op("secrets", "Vault code", operation="append", visibility="shared")]),
            source_rowid=11,
            primary_name="Alice",
            user_name="User",
        )
        assert result.applied == 0
        assert result.rejected == 1
        assert find_test_npc(db, "chat", "s1", "maya torres") is None
    finally:
        db.close()


@pytest.mark.parametrize("name", ["Alice", "User"])
def test_primary_character_and_user_identity_are_rejected(name):
    db = _db()
    service = NpcService()
    try:
        result = service.apply_group(
            db,
            "chat",
            "s1",
            _group(name=name, operations=[_op("role", "Rival")]),
            source_rowid=10,
            primary_name="Alice",
            user_name="User",
        )
        assert result.applied == 0
        assert result.rejected == 1
        assert list(db.execute("SELECT npc_id FROM npc_entities")) == []
    finally:
        db.close()


def test_alias_collision_rejects_group_instead_of_merging():
    db = _db()
    service = NpcService()
    try:
        service.apply_group(
            db,
            "chat",
            "s1",
            _group("Maya Torres", [_op("role", "Archivist")], aliases=("May",)),
            source_rowid=10,
            primary_name="Alice",
            user_name="User",
        )
        result = service.apply_group(
            db,
            "chat",
            "s1",
            _group("Maya Chen", [_op("role", "Guard")], aliases=("May",)),
            source_rowid=11,
            primary_name="Alice",
            user_name="User",
        )
        assert result.applied == 0
        assert result.rejected == 1
        assert find_test_npc(db, "chat", "s1", "maya chen") is None
    finally:
        db.close()


def test_rollback_restores_pre_edit_value_removes_future_entity_and_rewinds_coverage():
    db = _db()
    service = NpcService()
    try:
        service.apply_group(
            db,
            "chat",
            "s1",
            _group(operations=[_op("relationship", "cautious")]),
            source_rowid=100,
            primary_name="Alice",
            user_name="User",
        )
        service.apply_group(
            db,
            "chat",
            "s1",
            _group(operations=[_op("relationship", "hostile")]),
            source_rowid=150,
            primary_name="Alice",
            user_name="User",
        )
        service.apply_group(
            db,
            "chat",
            "s1",
            _group("Jon", [_op("role", "Courier")]),
            source_rowid=170,
            primary_name="Alice",
            user_name="User",
        )
        db.execute(
            "INSERT OR REPLACE INTO npc_extraction_state VALUES(?,?,?,?)",
            ("chat", "s1", 180, 1.0),
        )
        db.commit()

        removed = service.rollback_from_row(db, "chat", "s1", 140)

        maya = find_test_npc(db, "chat", "s1", "maya torres")
        assert load_npc_fields(db, maya.npc_id)["relationship"].value == "cautious"
        assert maya.last_seen_rowid == 100
        assert find_test_npc(db, "chat", "s1", "jon") is None
        assert (
            db.execute(
                "SELECT updated_through_rowid FROM npc_extraction_state WHERE chat_id='chat' AND session_id='s1'"
            ).fetchone()[0]
            == 139
        )
        assert removed >= 2
    finally:
        db.close()
