"""The existing NPC worker owns atomic tracker extraction and source proof."""

import json

from application_test_setup import make_test_provider_port
from test_memory_completion_safety import session_db as session_db
from test_simulation_trackers import _assistant_row

from bridge.npc_extraction import refresh_npc_state_now
from bridge.npc_repository import get_npc_extraction_coverage
from bridge.simulation_service import SimulationService


def refresh(case, generate):
    config, db, session = case
    return refresh_npc_state_now(
        db,
        "chat",
        session,
        {"name": "Alice"},
        provider_port=make_test_provider_port(generate_backend=generate),
        app_settings=config,
    )


def test_existing_npc_utility_call_publishes_simulation_without_a_second_model_call(session_db):
    _, db, _ = session_db
    source = _assistant_row(db, "The user receives a brass key.")
    seen = []

    def generate(_key, _model, messages, **kwargs):
        seen.append(messages)
        assert not db.in_transaction
        return json.dumps({"npcs": [], "simulation": {"actor": {"inventory_add": ["Brass key"]}}})

    refresh(session_db, generate)
    assert len(seen) == 1
    assert "simulation" in seen[0][0]["content"] and "bridge-owned" in seen[0][0]["content"]
    assert SimulationService().state(db, "chat", "s1", "actor", "user")["inventory"][0]["name"] == "Brass key"
    assert get_npc_extraction_coverage(db, "chat", "s1") == source


def test_malformed_simulation_does_not_advance_coverage(session_db):
    _, db, _ = session_db
    _assistant_row(db)
    refresh(session_db, lambda *a, **k: '{"npcs":[],"simulation":{"relationships":[{"npc":"Maya","apology":"false"}]}}')
    assert get_npc_extraction_coverage(db, "chat", "s1") == 0
    assert db.execute("SELECT COUNT(*) FROM simulation_sources").fetchone()[0] == 0


def test_valid_simulation_publishes_when_npc_section_is_malformed(session_db):
    _, db, _ = session_db
    source = _assistant_row(db, "The user receives a raincoat.")

    refresh(
        session_db,
        lambda *a, **k: json.dumps(
            {
                "npcs": "malformed",
                "simulation": {"actor": {"inventory_add": ["Raincoat"]}},
            }
        ),
    )

    actor = SimulationService().state(db, "chat", "s1", "actor", "user")
    assert actor is not None
    assert actor["inventory"][0]["name"] == "Raincoat"
    assert (
        db.execute(
            "SELECT MAX(source_rowid) FROM simulation_sources WHERE chat_id=? AND session_id=?",
            ("chat", "s1"),
        ).fetchone()[0]
        == source
    )
    assert get_npc_extraction_coverage(db, "chat", "s1") == 0


def test_malformed_combined_output_repairs_tracker_without_advancing_npc_coverage(session_db):
    _, db, _ = session_db
    source = _assistant_row(db, "The user receives a raincoat.")
    responses = [
        "not-json",
        json.dumps({"simulation": {"actor": {"inventory_add": ["Raincoat"]}}}),
    ]
    prompts = []

    def generate(_key, _model, messages, **_kwargs):
        prompts.append(messages)
        return responses.pop(0)

    refresh(session_db, generate)

    actor = SimulationService().state(db, "chat", "s1", "actor", "user")
    assert actor is not None
    assert actor["inventory"][0]["name"] == "Raincoat"
    assert len(prompts) == 2
    assert "tracker" in prompts[1][0]["content"].casefold()
    assert (
        db.execute(
            "SELECT MAX(source_rowid) FROM simulation_sources WHERE chat_id=? AND session_id=?",
            ("chat", "s1"),
        ).fetchone()[0]
        == source
    )
    assert get_npc_extraction_coverage(db, "chat", "s1") == 0


def test_malformed_npc_partial_tracker_publish_rejects_rewritten_source(session_db):
    _, db, _ = session_db
    source = _assistant_row(db, "The user receives a raincoat.")

    def generate(*_args, **_kwargs):
        db.execute("UPDATE messages SET content=? WHERE id=?", ("Rewritten story.", source))
        db.commit()
        return json.dumps(
            {
                "npcs": "malformed",
                "simulation": {"actor": {"inventory_add": ["Raincoat"]}},
            }
        )

    refresh(session_db, generate)

    assert SimulationService().state(db, "chat", "s1", "actor", "user") is None
    assert (
        db.execute(
            "SELECT COUNT(*) FROM simulation_sources WHERE chat_id=? AND session_id=?",
            ("chat", "s1"),
        ).fetchone()[0]
        == 0
    )
    assert get_npc_extraction_coverage(db, "chat", "s1") == 0


def test_missing_simulation_does_not_advance_coverage(session_db):
    _, db, _ = session_db
    _assistant_row(db)
    refresh(session_db, lambda *a, **k: '{"npcs":[]}')
    assert get_npc_extraction_coverage(db, "chat", "s1") == 0
    assert db.execute("SELECT COUNT(*) FROM simulation_sources").fetchone()[0] == 0


def test_invalid_optional_narrative_link_does_not_block_tracker_publication(session_db):
    _, db, _ = session_db
    source = _assistant_row(db, "The user receives a brass key while opening the gate.")

    def generate(*_args, **_kwargs):
        return json.dumps(
            {
                "npcs": [],
                "simulation": {
                    "actor": {"inventory_add": ["Brass key"]},
                    "quests": [
                        {
                            "id": "gate",
                            "kind": "main",
                            "status": "active",
                            "objective": "Open the gate",
                            "progress_current": 0,
                            "progress_target": 1,
                            "reward": "",
                            "arc_id": "invented-arc",
                        }
                    ],
                },
            }
        )

    refresh(session_db, generate)

    service = SimulationService()
    assert service.state(db, "chat", "s1", "actor", "user")["inventory"][0]["name"] == "Brass key"
    quest = service.state(db, "chat", "s1", "quest", "gate")
    assert quest["objective"] == "Open the gate"
    assert "arc_id" not in quest
    assert get_npc_extraction_coverage(db, "chat", "s1") == source


def test_parts_remain_private_until_the_complete_source_row(session_db):
    _, db, _ = session_db
    _assistant_row(db, "Long story. " * 1500)
    calls = []

    def generate(*args, **kwargs):
        assert SimulationService().state(db, "chat", "s1", "actor", "user") is None
        calls.append(1)
        operation = "add" if len(calls) == 1 else "remove"
        return json.dumps({"npcs": [], "simulation": {"actor": {f"inventory_{operation}": ["Transient key"]}}})

    refresh(session_db, generate)
    assert len(calls) == 2
    assert SimulationService().state(db, "chat", "s1", "actor", "user")["inventory"] == []


def test_director_and_choices_use_their_captured_tracker_boundary(session_db):
    from types import SimpleNamespace

    from bridge.director_prompt import build_director_input
    from bridge.director_repository import load_director_state
    from bridge.light_novel_service import build_choice_context_snapshot
    from bridge.narrative_values import NarrativeSettings, NarrativeState

    config, db, session = session_db
    first = _assistant_row(db)
    SimulationService().apply_payload(db, "chat", "s1", {"actor": {"inventory_add": ["Past key"]}}, source_rowid=first)
    later = _assistant_row(db)
    SimulationService().apply_payload(
        db, "chat", "s1", {"actor": {"inventory_add": ["Future map"]}}, source_rowid=later
    )
    choice = build_choice_context_snapshot(
        db,
        SimpleNamespace(chat_id="chat", session_id="s1", assistant_rowid=first),
        session,
        {"name": "Alice"},
        "Story.",
        app_settings=config,
    )
    director, *_ = build_director_input(
        db,
        "chat",
        session,
        NarrativeState(updated_through_rowid=first),
        NarrativeSettings(),
        load_director_state(db, "chat", "s1"),
        app_settings=config,
    )
    for payload in (choice, director):
        assert "Past key" in payload["simulation_state"] and "Future map" not in payload["simulation_state"]
