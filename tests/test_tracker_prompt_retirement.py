"""Retiring a prompt protocol must not replay or corrupt native story state."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from settings_test_support import make_test_settings
from test_simulation_trackers import _assistant_row, _db

from bridge import schema
from bridge.generation import build_chat_messages
from bridge.memory_draft_publish import restore_derived
from bridge.memory_draft_store import prepare_draft
from bridge.memory_store import claim_is_current, claim_jobs, next_source_segment
from bridge.npc_repository import get_npc_extraction_coverage, set_npc_extraction_coverage
from bridge.simulation_service import SimulationService
from bridge.sqlite_store import write_transaction
from bridge.telegram_output import telegram_safe_output, telegram_transport_output


def _database_at(monkeypatch, version):
    with monkeypatch.context() as old:
        old.setattr(schema, "SCHEMA_MIGRATIONS", tuple(m for m in schema.SCHEMA_MIGRATIONS if m.version <= version))
        return _db()


def _native_checkpoint(db, source):
    created = db.execute("SELECT created_at FROM sessions WHERE chat_id='chat' AND session_id='s1'").fetchone()[0]
    with write_transaction(db):
        set_npc_extraction_coverage(db, "chat", "s1", source, 1)
        db.execute(
            "INSERT OR REPLACE INTO memory_layer_state"
            "(chat_id,session_id,session_created_at,layer,covered_id,draft_json) VALUES(?,?,?,'npc',?,'{}')",
            ("chat", "s1", created, source),
        )
        db.execute("UPDATE memory_jobs SET completed_version=dirty_version WHERE layer='npc'")


def test_upgrade_does_not_replay_previously_processed_native_sources(monkeypatch):
    db = _database_at(monkeypatch, 24)
    try:
        source = _assistant_row(db, "Maya works as a doctor.")
        _native_checkpoint(db, source)
        before = db.execute("SELECT dirty_version,completed_version FROM memory_jobs WHERE layer='npc'").fetchone()
        schema.initialize_database_schema(db)
        assert get_npc_extraction_coverage(db, "chat", "s1") == source
        assert next_source_segment(db, "chat", "s1", "npc") is None
        assert (
            db.execute("SELECT dirty_version,completed_version FROM memory_jobs WHERE layer='npc'").fetchone() == before
        )
        later = _assistant_row(db, "Maya hands over a brass key.")
        assert next_source_segment(db, "chat", "s1", "npc").start_id == later
    finally:
        db.close()


@pytest.mark.parametrize("rewrite", [False, True])
def test_installed_bootstrap_is_retired_without_skipping_new_or_rewritten_sources(monkeypatch, rewrite):
    db = _database_at(monkeypatch, 25)
    try:
        source = _assistant_row(db, "Maya works as a doctor.")
        _native_checkpoint(db, source)
        SimulationService().apply_payload(
            db, "chat", "s1", {"actor": {"inventory_add": ["Brass key"]}}, source_rowid=source
        )
        later = _assistant_row(db, "The user walks toward the gate.")
        with write_transaction(db):
            db.execute(
                "INSERT INTO simulation_revisions(chat_id,session_id,backfill_through) VALUES('chat','s1',?) "
                "ON CONFLICT(chat_id,session_id) DO UPDATE SET backfill_through=excluded.backfill_through",
                (source,),
            )
            db.execute("UPDATE npc_extraction_state SET updated_through_rowid=0")
            db.execute("UPDATE memory_layer_state SET covered_id=0,draft_json='' WHERE layer='npc'")
            db.execute("UPDATE memory_jobs SET completed_version=0 WHERE layer='npc'")
        if rewrite:
            with write_transaction(db):
                db.execute("UPDATE messages SET content='Maya is now a teacher.' WHERE id=?", (source,))
        stale = claim_jobs(db, layers=("npc",), limit=1)[0]
        schema.initialize_database_schema(db)
        assert not claim_is_current(db, stale)
        expected = 0 if rewrite else source
        assert get_npc_extraction_coverage(db, "chat", "s1") == expected
        fresh = claim_jobs(db, layers=("npc",), limit=1)[0]
        draft = prepare_draft(
            db,
            fresh,
            valid=lambda: claim_is_current(db, fresh),
            restore=lambda payload, through: restore_derived(db, "chat", "s1", "npc", payload, through),
        )
        assert draft is not None and draft.through_id == expected
        assert next_source_segment(db, "chat", "s1", "npc").start_id == (source if rewrite else later)
        if not rewrite:
            assert SimulationService().state(db, "chat", "s1", "actor", "user")["inventory"][0]["name"] == "Brass key"
    finally:
        db.close()


def test_retired_numeric_ledger_cannot_seed_native_relationships():
    db = _db()
    try:
        source = _assistant_row(db, "<internal_states>Maya: BOND=8 Sparks=3 Grudge=2</internal_states>")
        SimulationService().apply_payload(
            db,
            "chat",
            "s1",
            {
                "relationships": [
                    {
                        "npc": "Maya",
                        "baseline": {"bond": 8, "sparks": 3, "grudge": 2, "quote": "Maya: BOND=8 Sparks=3 Grudge=2"},
                    }
                ]
            },
            source_rowid=source,
        )
        state = SimulationService().state(db, "chat", "s1", "relationship", "maya")
        assert state["bond"] == 0 and state["sparks"] == 0 and state["grudge"] == 0
    finally:
        db.close()


def test_native_prompt_and_history_are_not_rewritten_as_a_retired_protocol(tmp_path):
    template = "Keep this example.\n## Internal States\nOutput BOND Sparks Grudge.\n## Dialogue\nUse distinct voices."
    session = {
        "session_id": "s1",
        "model_id": "p::m",
        "persona_id": "",
        "world_file": "",
        "author_note": "",
        "system_prompt": template,
        "response_language": "en",
    }
    fields = {
        "name": "Alice",
        "description": "",
        "personality": "",
        "scenario": "",
        "first_mes": "",
        "mes_example": "",
        "system_prompt": "",
        "post_history_instructions": "",
        "alternate_greetings": "[]",
    }
    history = [("assistant", "An example: <internal_states>Literary passage.</internal_states>")]
    original = deepcopy((session, fields, history))
    messages = build_chat_messages(
        session,
        fields,
        "Continue.",
        history,
        persona_service=SimpleNamespace(),
        app_settings=make_test_settings(home=tmp_path),
        defer_compaction=True,
    )
    assert template in messages[0]["content"]
    assert messages[1]["content"] == history[0][1]
    assert (session, fields, history) == original


@pytest.mark.parametrize("renderer", [telegram_safe_output, telegram_transport_output])
def test_native_html_formatting_does_not_discard_unknown_element_contents(renderer):
    assert renderer("Before.<internal_states>Literary passage.</internal_states>After.") == (
        "Before.Literary passage.After."
    )


def _mark_installed_bootstrap(db, source):
    with write_transaction(db):
        db.execute(
            "INSERT INTO simulation_revisions(chat_id,session_id,backfill_through) VALUES('chat','s1',?) "
            "ON CONFLICT(chat_id,session_id) DO UPDATE SET backfill_through=excluded.backfill_through",
            (source,),
        )


def _prepare_npc(db):
    claim = claim_jobs(db, layers=("npc",), limit=1)[0]
    draft = prepare_draft(
        db,
        claim,
        valid=lambda: claim_is_current(db, claim),
        restore=lambda payload, through: restore_derived(db, "chat", "s1", "npc", payload, through),
    )
    assert draft is not None
    return claim, draft


def test_retirement_restarts_a_partial_new_row_when_discarding_its_accumulator(monkeypatch):
    from bridge.memory_draft_publish import publish_derived
    from bridge.memory_draft_store import accept_draft_part, load_draft

    db = _database_at(monkeypatch, 25)
    try:
        old = _assistant_row(db, "Previously processed native story.")
        _native_checkpoint(db, old)
        later = _assistant_row(db, "x" * 12000 + "tail")
        _mark_installed_bootstrap(db, old)
        claim, draft = _prepare_npc(db)
        source = next_source_segment(db, "chat", "s1", "npc")
        assert source.start_id == later and source.start_offset == 0 and source.end_offset == 12000
        assert accept_draft_part(
            db,
            claim,
            source,
            draft,
            {"simulation": {"actor": {"inventory_add": ["Brass key"]}}},
            valid=lambda: claim_is_current(db, claim),
            publish=lambda *_: pytest.fail("An incomplete row cannot publish"),
            completed_payload={},
        )
        schema.initialize_database_schema(db)
        assert not claim_is_current(db, claim)
        fresh, resumed = _prepare_npc(db)
        assert resumed.payload == {} and resumed.through_id == old
        replay = next_source_segment(db, "chat", "s1", "npc")
        assert replay.start_id == later and replay.start_offset == 0
        payload = {"simulation": {"actor": {"inventory_add": ["Brass key"]}}}
        for _ in range(2):
            source = next_source_segment(db, "chat", "s1", "npc")
            assert accept_draft_part(
                db,
                fresh,
                source,
                load_draft(db, fresh),
                payload,
                valid=lambda: claim_is_current(db, fresh),
                publish=lambda result, through: publish_derived(db, "chat", "s1", "npc", result, through),
                completed_payload={},
            )
        assert next_source_segment(db, "chat", "s1", "npc") is None
        assert SimulationService().state(db, "chat", "s1", "actor", "user")["inventory"][0]["name"] == "Brass key"
    finally:
        db.close()


def test_retirement_rolls_back_a_rewritten_suffix_after_a_valid_native_prefix(monkeypatch):
    from test_npc_service import _group, _op

    from bridge.memory_draft_publish import publish_derived
    from bridge.npc_repository import find_npc_by_name_or_alias, load_npc_fields
    from bridge.npc_service import NpcService

    db = _database_at(monkeypatch, 25)
    try:
        first = _assistant_row(db, "Maya is a doctor. The user receives a brass key.")
        later = _assistant_row(db, "Maya becomes a smuggler. The user receives a map.")
        for source, role, item in ((first, "Doctor", "Brass key"), (later, "Smuggler", "Map")):
            NpcService().apply_group(
                db,
                "chat",
                "s1",
                _group(name="Maya", operations=[_op("role", role)]),
                source_rowid=source,
                primary_name="Alice",
                user_name="User",
            )
            SimulationService().apply_payload(
                db,
                "chat",
                "s1",
                {"actor": {"inventory_add": [item]}},
                source_rowid=source,
            )
        _native_checkpoint(db, later)
        _mark_installed_bootstrap(db, later)
        with write_transaction(db):
            db.execute("UPDATE messages SET content='Maya remains a doctor.' WHERE id=?", (later,))
        schema.initialize_database_schema(db)
        _, resumed = _prepare_npc(db)
        assert resumed.through_id == first
        inventory = SimulationService().state(db, "chat", "s1", "actor", "user")["inventory"]
        assert [item["name"] for item in inventory] == ["Brass key"]
        npc = find_npc_by_name_or_alias(db, "chat", "s1", "Maya")
        assert load_npc_fields(db, npc.npc_id)["role"].value == "Doctor"
        with write_transaction(db):
            publish_derived(db, "chat", "s1", "npc", {}, later)
        assert get_npc_extraction_coverage(db, "chat", "s1") == later
    finally:
        db.close()


@pytest.mark.parametrize("marker", ["🎬", "🎽", "🎯", "*🎬", "*🎽"])
def test_migration_27_cleans_retired_tracker_wrapper_without_losing_native_state(monkeypatch, marker):
    from bridge.memory_store import pending_memory_invalidation
    from bridge.simulation_repository import source_identity

    db = _database_at(monkeypatch, 26)
    try:
        story = '*Alex enters the room.*\n\n"Hello."'
        retired = (
            f"\n\n<tg-spoiler>\n{marker} INTERNAL STATES (Turn: 7)\n"
            "NPC AGENDAS\n- Alex | Buy groceries | (Step 2/5)\n"
            "NPC LOCATIONS\n- Alex | Store\n"
            "BONDS\n- Alex ↔ User | BOND: 9 | Sparks: 1 | Grudge: 0\n"
            "QUESTS\n- Main: Active — Shopping\n"
            "INV & SKILLS\n- Inv: Phone\n"
            "PHYSICS\n- Room\n"
            "</tg-spoiler>"
        )
        source = _assistant_row(db, story + retired)
        _native_checkpoint(db, source)
        SimulationService().apply_payload(
            db,
            "chat",
            "s1",
            {"actor": {"inventory_add": ["Brass key"]}},
            source_rowid=source,
        )
        old_digest = db.execute(
            "SELECT source_digest FROM simulation_sources WHERE chat_id='chat' AND session_id='s1' AND source_rowid=?",
            (source,),
        ).fetchone()[0]

        schema.initialize_database_schema(db)

        assert db.execute("SELECT content FROM messages WHERE id=?", (source,)).fetchone()[0] == story
        assert SimulationService().state(db, "chat", "s1", "actor", "user")["inventory"][0]["name"] == "Brass key"
        assert pending_memory_invalidation(db, "chat", "s1", "npc") is None
        _, new_digest = source_identity(db, "chat", "s1", source)
        assert new_digest != old_digest
        assert db.execute(
            "SELECT source_digest FROM simulation_sources WHERE chat_id='chat' AND session_id='s1' AND source_rowid=?",
            (source,),
        ).fetchone()[0] == new_digest
    finally:
        db.close()


def test_migration_27_keeps_unrelated_internal_states_prose(monkeypatch):
    db = _database_at(monkeypatch, 26)
    try:
        content = "<tg-spoiler>A character reflects on internal states without a tracker ledger.</tg-spoiler>"
        source = _assistant_row(db, content)
        schema.initialize_database_schema(db)
        assert db.execute("SELECT content FROM messages WHERE id=?", (source,)).fetchone()[0] == content
    finally:
        db.close()
