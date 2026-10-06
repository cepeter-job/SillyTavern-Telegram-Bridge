"""Player-facing projections must not expose private or invalidated canonical state."""

import json
from contextlib import closing

import pytest
from test_npc_service import _op
from test_simulation_projection import npc
from test_simulation_trackers import _assistant_row, _db
from tracker_view_test_support import seed_trackers

from bridge.simulation_service import SimulationService


@pytest.fixture
def db():
    with closing(_db()) as connection:
        yield connection


def view(db, **kwargs):
    from bridge.simulation_view import tracker_view

    return tracker_view(db, "chat", "s1", active_character="Alice", **kwargs)


def test_projects_saved_values_and_omits_private_metadata(db):
    source = seed_trackers(db, "chat", "s1")
    result = view(db)
    assert result["relationships"] == [
        {"name": "Maya <guide>", "bond": -1, "tier": "neutral", "sparks": 2, "grudge": 1}
    ]
    assert result["inventory"] == [{"name": "Brass key", "domain": "stealth", "modifier": 1}]
    assert result["skills"] == [{"name": "Persuasion", "domain": "social", "modifier": 2}]
    assert result["conditions"] == [{"name": "Tired", "domain": "any", "modifier": -1}]
    assert result["agendas"] == [
        {
            "name": "Rowan",
            "objective": "Reach <b>the tower</b>",
            "step": 1,
            "max_steps": 3,
            "status": "active",
            "location": "North ridge",
        }
    ]
    assert result["factions"] == [
        {"name": "Harbor Watch", "goal": "Protect the docks", "morale": "Steady", "conflict": "Smugglers"}
    ]
    assert result["quests"][0]["progress_current"] == 1
    assert result["quests"][0]["progress_target"] == 3
    assert result["checks"] == [
        {
            "domain": "stealth",
            "actor": "user",
            "action": "Open the locked door",
            "dc": 10,
            "roll": 12,
            "modifier": 1,
            "delta": 3,
            "outcome": "success",
        }
    ]
    assert result["last_source_rowid"] == source
    assert result["last_updated_at"] > 0
    assert result["pending"] is False
    serialized = json.dumps(result)
    for private in ("Secret", "_projection", "source_digest", "request_key", "private-operation-key"):
        assert private not in serialized


def test_reading_twice_never_writes_or_advances_saved_mechanics(db):
    seed_trackers(db, "chat", "s1")
    before = db.total_changes
    db.execute("PRAGMA query_only=ON")
    first = view(db)
    second = view(db)
    assert first == second
    assert db.total_changes == before
    assert first["agendas"][0]["step"] == 1
    assert first["checks"][0]["roll"] == 12
    assert not db.in_transaction


def test_new_story_content_is_pending_until_accepted_without_extraction(db):
    source = seed_trackers(db, "chat", "s1")
    _assistant_row(db, "Another story beat is waiting for extraction.")
    result = view(db)
    assert result["pending"] is True
    assert result["last_source_rowid"] == source
    assert result["agendas"][0]["step"] == 1


def test_rewritten_source_hides_state_and_checks_before_worker_repair(db):
    source = seed_trackers(db, "chat", "s1")
    db.execute("UPDATE messages SET content='The earlier event was replaced.' WHERE id=?", (source,))
    db.commit()
    result = view(db)
    assert result["relationships"] == result["inventory"] == result["checks"] == []
    assert result["pending"] is True
    assert result["last_source_rowid"] == 0


@pytest.mark.parametrize("restricted_first", [True, False])
def test_npc_visibility_must_allow_both_saved_and_current_audiences(db, restricted_first):
    first = _assistant_row(db)
    npc(
        db,
        first,
        [
            _op(
                "relationship",
                "First native relationship",
                visibility="restricted" if restricted_first else "shared",
                known_by=["Maya Torres"],
            )
        ],
    )
    SimulationService().apply_payload(
        db,
        "chat",
        "s1",
        {"relationships": [{"npc": "Maya", "sparks_delta": 2}]},
        source_rowid=first,
    )
    second = _assistant_row(db)
    npc(
        db,
        second,
        [
            _op(
                "relationship",
                "Later native relationship",
                visibility="shared" if restricted_first else "restricted",
                known_by=["Maya Torres"],
            )
        ],
    )
    assert view(db, through_rowid=first)["relationships"] == []


def test_restricted_agenda_visible_to_active_character_uses_npc_bank_name_rules(db):
    first = _assistant_row(db)
    npc(db, first, [_op("agenda", "Known objective", visibility="restricted", known_by=["  ALICE  "])])
    SimulationService().apply_payload(
        db,
        "chat",
        "s1",
        {"agendas": [{"npc": "Maya", "objective": "Find the witness", "max_steps": 3}]},
        source_rowid=first,
    )
    assert view(db)["agendas"][0]["objective"] == "Find the witness"


def test_projection_does_not_cross_chat_or_session_boundaries(db):
    seed_trackers(db, "chat", "s1")
    from bridge.simulation_view import tracker_view

    for chat_id, session_id in (("foreign", "s1"), ("chat", "foreign")):
        result = tracker_view(db, chat_id, session_id, active_character="Alice")
        assert result["relationships"] == result["quests"] == result["checks"] == []


def test_tracker_command_is_registered_beside_existing_status(monkeypatch):
    from bridge.bot_commands import set_bot_commands

    requests = []
    monkeypatch.setattr(
        "bridge.bot_commands.telegram_request", lambda token, method, payload: requests.append((method, payload))
    )
    set_bot_commands("synthetic-token")
    commands = next(payload["commands"] for method, payload in requests if method == "setMyCommands")
    assert sum(item["command"] == "trackers" for item in commands) == 1
    assert sum(item["command"] == "status" for item in commands) == 1


def test_tracker_renderer_preserves_scores_and_bounds_large_unicode_output():
    from test_simulation_trackers import _db
    from tracker_view_test_support import seed_trackers

    from bridge.simulation_view import tracker_view
    from bridge.simulation_view_output import format_tracker_view

    db = _db()
    try:
        seed_trackers(db, "chat", "s1")
        saved = tracker_view(db, "chat", "s1", active_character="Alice") | {
            "session": {"session_id": "s1", "title": "Current story"},
        }
        rendered = format_tracker_view(saved)
        for detail in ("BOND -1", "Sparks 2", "Grudge 1", "Brass key", "Persuasion", "Tired", "DC 10"):
            assert detail in rendered
        assert "Secret" not in rendered
        for section in (
            "relationships",
            "agendas",
            "inventory",
            "skills",
            "conditions",
            "factions",
            "quests",
            "checks",
        ):
            template = saved[section][0]
            saved[section] = [
                {key: ("😀" * 500 if isinstance(value, str) else value) for key, value in template.items()}
                for _ in range(64 if section != "checks" else 8)
            ]
        bounded = format_tracker_view(saved)
        assert len(bounded.encode("utf-16-le")) // 2 <= 4096
        for heading in ("Relationships", "Agendas", "Inventory", "Skills", "Conditions", "Factions", "Quests", "d20"):
            assert heading in bounded
        assert "62 more" in bounded and "6 more" in bounded
        assert "Mini App" in bounded and "shortened" in bounded
    finally:
        db.close()


def test_registered_tracker_extension_reads_active_state_without_consuming_a_turn(db, monkeypatch, tmp_path):
    from settings_test_support import make_test_settings

    import bridge.extension_registry as registry
    from bridge.delivery_port import DeliveryPort
    from bridge.meta_repository import store_meta_value
    from bridge.provider_port import ProviderPort
    from bridge.request_types import RequestContext
    from bridge.simulation_commands import register_simulation_extensions
    from bridge.sqlite_store import write_transaction

    seed_trackers(db, "chat", "s1")
    with write_transaction(db):
        store_meta_value(db, "active_session:chat", "s1")
    monkeypatch.setattr(registry, "_COMMAND_ROUTES", {})
    register_simulation_extensions()
    sent = []

    def forbidden(*args, **kwargs):
        pytest.fail("Tracker inspection must not call providers or other delivery operations")

    def send(token, chat_id, message):
        sent.append((chat_id, message))
        return []

    delivery = DeliveryPort(forbidden, send, forbidden, forbidden, forbidden, forbidden)
    context = RequestContext(db, "s1", "owner", app_settings=make_test_settings(home=tmp_path))
    before = db.total_changes
    db.execute("PRAGMA query_only=ON")
    handled = registry.dispatch_command_routes(
        db,
        "token",
        "key",
        "model",
        {"name": "Alice"},
        "chat",
        "/trackers",
        "/trackers",
        {"session_id": "s1"},
        "s1",
        "model",
        "",
        "User",
        request_context=context,
        delivery_port=delivery,
        provider_port=ProviderPort(generate_backend=forbidden),
    )
    assert handled is True
    assert sent[0][0] == "chat" and "Brass key" in sent[0][1]
    assert "Secret" not in sent[0][1]
    assert db.total_changes == before


def test_linked_quest_status_follows_current_narrative_owner(db):
    from bridge.narrative_arc_repository import load_arc_row

    seed_trackers(db, "chat", "s1")
    quest = next(item for item in view(db)["quests"] if item["name"] == "watchtower-route")
    assert quest["narrative_linked"] is True
    assert quest["status"] == "native=active"
    assert (quest["progress_current"], quest["progress_target"]) == (2, 4)
    assert load_arc_row(db, "chat", "s1", "watchtower")["status"] == "active"
    _assistant_row(db, "The next scene is waiting for Narrative reconciliation.")
    pending = next(item for item in view(db)["quests"] if item["name"] == "watchtower-route")
    assert pending["status"] == "native=unavailable"


def test_trackers_remains_available_through_the_closed_story_input_guard(db):
    from bridge.closed_session_guard import closed_session_allows_input, story_mutation_message

    db.execute("INSERT INTO ending_state(chat_id,session_id,lifecycle) VALUES('chat','s1','closed')")
    db.commit()
    assert story_mutation_message(db, "chat", "s1") is not None
    assert closed_session_allows_input(db, "chat", "s1", "owner", "/trackers") is True
