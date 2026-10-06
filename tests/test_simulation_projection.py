"""Canonical NPC ownership and Narrative status stay authoritative."""

import pytest
from test_npc_service import _group, _op
from test_simulation_trackers import _assistant_row, _db

from bridge.npc_repository import find_npc_by_name_or_alias, load_npc_fields
from bridge.npc_service import NpcService
from bridge.simulation_service import SimulationService
from bridge.sqlite_store import write_transaction


@pytest.fixture
def db():
    connection = _db()
    yield connection
    connection.close()


def npc(db, source, operations=()):
    NpcService().apply_group(
        db,
        "chat",
        "s1",
        _group(operations=operations or [_op("role", "Guard")], aliases=["Maya"]),
        source_rowid=source,
        primary_name="Alice",
        user_name="User",
    )
    return find_npc_by_name_or_alias(db, "chat", "s1", "Maya")


def agenda(db, source, **changes):
    SimulationService().apply_payload(db, "chat", "s1", {"agendas": [dict(npc="Maya", **changes)]}, source_rowid=source)


def test_aliases_share_one_canonical_tracker_and_turn_limit(db):
    source = _assistant_row(db)
    npc(db, source)
    SimulationService().apply_payload(
        db,
        "chat",
        "s1",
        {"relationships": [{"npc": "Maya", "sparks_delta": 2}, {"npc": "Maya Torres", "sparks_delta": 2}]},
        source_rowid=source,
    )
    rows = db.execute("SELECT entity_key,value_json FROM simulation_state WHERE kind='relationship'").fetchall()
    assert len(rows) == 1 and rows[0][0] == "maya torres"
    assert SimulationService().state(db, "chat", "s1", "relationship", "maya torres")["sparks"] == 2


def test_projection_updates_only_its_owned_field_and_preserves_private_audience(db):
    first = _assistant_row(db)
    entity = npc(db, first)
    agenda(db, first, objective="Read the letter", max_steps=5)
    projected = load_npc_fields(db, entity.npc_id)["agenda"]
    assert "Read the letter" in projected.value and "0/5" in projected.value
    assert projected.visibility == "restricted" and projected.known_by == ("Maya Torres",)
    second = _assistant_row(db)
    agenda(db, second, step=2)
    assert "2/5" in load_npc_fields(db, entity.npc_id)["agenda"].value
    third = _assistant_row(db)
    npc(db, third, [_op("agenda", "User override", visibility="restricted", known_by=["Alice"])])
    agenda(db, third, step=3)
    field = load_npc_fields(db, entity.npc_id)["agenda"]
    assert (field.value, field.known_by) == ("User override", ("Alice",))


@pytest.mark.parametrize("visibility", ["shared", "restricted"])
def test_existing_unowned_npc_agenda_is_preserved(db, visibility):
    first = _assistant_row(db)
    entity = npc(db, first, [_op("agenda", "Existing goal", visibility=visibility, known_by=["Alice"])])
    before = load_npc_fields(db, entity.npc_id)["agenda"]
    agenda(db, first, objective="New goal", max_steps=5)
    assert load_npc_fields(db, entity.npc_id)["agenda"] == before


def test_primary_and_user_names_do_not_become_npc_trackers(db):
    source = _assistant_row(db)
    SimulationService().apply_payload(
        db,
        "chat",
        "s1",
        {"relationships": [{"npc": "Alice", "sparks_delta": 2}, {"npc": "User", "sparks_delta": 2}]},
        source_rowid=source,
        primary_name="Alice",
        user_name="User",
    )
    assert db.execute("SELECT COUNT(*) FROM simulation_state").fetchone()[0] == 0


def test_ambiguous_alias_creates_no_npc_or_projection(db):
    source = _assistant_row(db)
    npc(db, source)
    with write_transaction(db):
        db.execute(
            "INSERT INTO npc_entities(chat_id,session_id,canonical_name,display_name,aliases_json,"
            "first_seen_rowid,last_seen_rowid,created_at,updated_at) VALUES('chat','s1','other','Other',"
            "'[\"Maya\"]',?,?,1,1)",
            (source, source),
        )
    agenda(db, source, objective="Read", max_steps=5)
    assert db.execute("SELECT COUNT(*) FROM npc_entities").fetchone()[0] == 2
    assert db.execute("SELECT COUNT(*) FROM npc_fields WHERE field_key='agenda'").fetchone()[0] == 0


def test_tracker_terminal_status_cannot_resolve_a_native_arc(db):
    from bridge.narrative_arc_repository import load_arc_row, store_arc_row
    from bridge.narrative_repository import upsert_narrative_state_if_fresh

    source = _assistant_row(db)
    with write_transaction(db):
        store_arc_row(
            db,
            "chat",
            "s1",
            dict(
                arc_id="gate",
                title="Gate",
                status="active",
                phase="setup",
                importance="major",
                summary="Gate remains locked",
                open_questions=[],
                related_threads=[],
                source_revision=source,
                evidence=[],
            ),
        )
        revision = db.execute(
            "SELECT state_revision FROM narrative_state WHERE chat_id='chat' AND session_id='s1'"
        ).fetchone()[0]
        upsert_narrative_state_if_fresh(db, "chat", "s1", "{}", revision, source, 1)

    SimulationService().apply_payload(
        db,
        "chat",
        "s1",
        {"quests": [{"id": "gate-quest", "arc_id": "gate", "status": "completed", "objective": "Unlock gate"}]},
        source_rowid=source,
    )
    context = SimulationService().context_for_prompt(db, "chat", "s1")
    assert "native=active" in context and "completed" not in context
    assert load_arc_row(db, "chat", "s1", "gate")["status"] == "active"


def test_unknown_narrative_link_cannot_claim_a_foreign_plot(db):
    source = _assistant_row(db)
    with pytest.raises(ValueError, match="Narrative"):
        SimulationService().apply_payload(
            db,
            "chat",
            "s1",
            {"quests": [{"id": "quest", "arc_id": "missing", "objective": "Gate"}]},
            source_rowid=source,
        )
    assert db.execute("SELECT COUNT(*) FROM simulation_sources").fetchone()[0] == 0


def test_checkpoint_preserves_historical_projected_field_before_later_tick(db):
    from bridge.npc_repository import snapshot_npc_state

    first = _assistant_row(db)
    npc(db, first)
    agenda(db, first, objective="Read", max_steps=5)
    second = _assistant_row(db)
    SimulationService().apply_payload(db, "chat", "s1", {}, source_rowid=second)
    captured = snapshot_npc_state(db, "chat", "s1", first)
    fields = {field["field_key"]: field for field in captured[0]["fields"]}
    assert "0/5" in fields["agenda"]["value_json"]
