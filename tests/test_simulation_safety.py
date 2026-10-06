import pytest
from test_simulation_trackers import _assistant_row, _db

from bridge.simulation_service import SimulationService


@pytest.fixture
def db():
    connection = _db()
    try:
        yield connection
    finally:
        connection.close()


def test_replayed_row_does_not_repeat_relationship_delta(db):
    service = SimulationService()
    row = _assistant_row(db)
    payload = {"relationships": [{"npc": "Maya", "sparks_delta": 2}]}
    service.apply_payload(db, "chat", "s1", payload, source_rowid=row)
    before = db.execute("SELECT COUNT(*) FROM simulation_state_history").fetchone()[0]
    service.apply_payload(db, "chat", "s1", payload, source_rowid=row)
    assert service.state(db, "chat", "s1", "relationship", "maya")["sparks"] == 2
    assert db.execute("SELECT COUNT(*) FROM simulation_state_history").fetchone()[0] == before


def test_replayed_empty_row_does_not_repeat_agenda_tick(db):
    service = SimulationService()
    first = _assistant_row(db)
    service.apply_payload(
        db, "chat", "s1", {"agendas": [{"npc": "Maya", "objective": "Read", "max_steps": 5}]}, source_rowid=first
    )
    second = _assistant_row(db)
    service.apply_payload(db, "chat", "s1", {}, source_rowid=second)
    service.apply_payload(db, "chat", "s1", {}, source_rowid=second)
    assert service.state(db, "chat", "s1", "agenda", "maya")["step"] == 1


def test_user_row_never_ticks_offscreen_agenda(db):
    service = SimulationService()
    first = _assistant_row(db)
    service.apply_payload(
        db, "chat", "s1", {"agendas": [{"npc": "Maya", "objective": "Read", "max_steps": 5}]}, source_rowid=first
    )
    user = db.execute(
        "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('chat','s1','user','Wait',2)"
    ).lastrowid
    db.commit()
    service.apply_payload(db, "chat", "s1", {}, source_rowid=user)
    assert service.state(db, "chat", "s1", "agenda", "maya")["step"] == 0


def test_user_row_after_third_assistant_does_not_repeat_grudge_decay(db):
    service = SimulationService()
    for _ in range(3):
        source = _assistant_row(db)
        service.apply_payload(
            db, "chat", "s1", {"relationships": [{"npc": "Maya", "grudge_delta": 1}]}, source_rowid=source
        )
    assert service.state(db, "chat", "s1", "relationship", "maya")["grudge"] == 2
    user = db.execute(
        "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('chat','s1','user','Wait',2)"
    ).lastrowid
    db.commit()
    service.apply_payload(db, "chat", "s1", {}, source_rowid=user)
    assert service.state(db, "chat", "s1", "relationship", "maya")["grudge"] == 2


@pytest.mark.parametrize("source", [0, 999])
def test_payload_rejects_missing_source_without_state(db, source):
    with pytest.raises(ValueError, match="source"):
        SimulationService().apply_payload(
            db, "chat", "s1", {"relationships": [{"npc": "Maya", "sparks_delta": 1}]}, source_rowid=source
        )
    assert db.execute("SELECT COUNT(*) FROM simulation_state").fetchone()[0] == 0


def test_older_publication_cannot_corrupt_as_of_or_rollback(db):
    service = SimulationService()
    first, second, third = (_assistant_row(db) for _ in range(3))
    for source, objective in [(first, "First"), (third, "Third")]:
        service.apply_payload(
            db, "chat", "s1", {"quests": [{"id": "gate", "objective": objective}]}, source_rowid=source
        )
    with pytest.raises(ValueError, match=r"older|order"):
        service.apply_payload(
            db, "chat", "s1", {"quests": [{"id": "gate", "objective": "Late second"}]}, source_rowid=second
        )
    assert service.state(db, "chat", "s1", "quest", "gate")["objective"] == "Third"
    service.rollback_from_row(db, "chat", "s1", second)
    assert service.state(db, "chat", "s1", "quest", "gate")["objective"] == "First"


def test_same_row_rewrite_hides_stale_context_before_worker_repair(db):
    service = SimulationService()
    source = _assistant_row(db, "Maya gives a key.")
    service.apply_payload(db, "chat", "s1", {"actor": {"inventory_add": ["Secret key"]}}, source_rowid=source)
    db.execute("UPDATE messages SET content='Maya leaves empty handed.' WHERE id=?", (source,))
    db.commit()
    assert "Secret key" not in service.context_for_prompt(db, "chat", "s1")
    with pytest.raises(ValueError, match=r"rewritten|invalidat"):
        service.apply_payload(db, "chat", "s1", {}, source_rowid=source)
    service.rollback_from_row(db, "chat", "s1", source)
    service.apply_payload(db, "chat", "s1", {}, source_rowid=source)
    assert service.state(db, "chat", "s1", "actor", "user") is None


def test_check_rejects_nonexistent_source(db):
    _assistant_row(db)
    with pytest.raises(ValueError, match="source"):
        SimulationService().perform_check(
            db,
            "chat",
            "s1",
            request_key="check:missing",
            domain="social",
            actor="User",
            action="Ask",
            dc=10,
            source_rowid=999,
        )


def test_historical_check_does_not_use_future_skill(db):
    service = SimulationService()
    first = _assistant_row(db)
    second = _assistant_row(db)
    service.apply_payload(
        db,
        "chat",
        "s1",
        {"actor": {"skills_add": [{"name": "Charm", "domain": "social", "modifier": 2}]}},
        source_rowid=second,
    )
    result = service.perform_check(
        db,
        "chat",
        "s1",
        request_key="check:past",
        domain="social",
        actor="User",
        action="Ask",
        dc=10,
        roll=9,
        source_rowid=first,
    )
    assert result["modifier"] == 0
    assert result["outcome"] == "near_miss"


def test_npc_check_cannot_borrow_user_skill(db):
    service = SimulationService()
    source = _assistant_row(db)
    service.apply_payload(
        db,
        "chat",
        "s1",
        {"actor": {"skills_add": [{"name": "Charm", "domain": "social", "modifier": 2}]}},
        source_rowid=source,
    )
    result = service.perform_check(
        db,
        "chat",
        "s1",
        request_key="check:npc",
        domain="social",
        actor="Maya",
        action="Ask",
        dc=10,
        roll=9,
    )
    assert result["modifier"] == 0


def test_context_reserves_space_for_checks_relationships_and_quests(db):
    service = SimulationService()
    source = _assistant_row(db)
    service.apply_payload(
        db,
        "chat",
        "s1",
        {
            "agendas": [{"npc": f"NPC {i}", "objective": "a" * 500, "max_steps": 20} for i in range(20)],
            "relationships": [{"npc": "Maya", "sparks_delta": 1}],
            "quests": [{"id": "gate", "objective": "Open the gate", "status": "active"}],
        },
        source_rowid=source,
    )
    service.perform_check(
        db,
        "chat",
        "s1",
        request_key="check:gate",
        domain="physical",
        actor="User",
        action="Open gate",
        dc=10,
        roll=11,
    )
    context = service.context_for_prompt(db, "chat", "s1")
    assert len(context) <= 6000
    assert "CHECK physical" in context
    assert "REL Maya" in context
    assert "Open the gate" in context
