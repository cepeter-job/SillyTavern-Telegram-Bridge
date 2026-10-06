"""Real-state regressions from whole-branch tracker review."""

import json
from dataclasses import replace

import pytest
from test_director_service import directed as directed
from test_director_service import reassess, response
from test_light_novel_generation import story_row
from test_light_novel_storage import novel_db as novel_db
from test_memory_completion_safety import session_db as session_db
from test_npc_service import _group, _op
from test_simulation_integration import refresh
from test_simulation_projection import npc
from test_simulation_trackers import _assistant_row

from bridge.checkpoint_remap import remap_checkpoint
from bridge.context_compaction import context_profile, estimate_message_tokens
from bridge.director_prompt import DIRECTOR_INSTRUCTION, build_director_input
from bridge.director_repository import load_director_state
from bridge.narrative_context import load_narrative_state
from bridge.narrative_repository import load_narrative_clock
from bridge.narrative_settings import load_session_narrative_settings
from bridge.npc_repository import get_npc_extraction_coverage, list_npc_entities, load_npc_fields
from bridge.npc_service import NpcService
from bridge.provider_port import ProviderPort
from bridge.session_core import create_session
from bridge.simulation_extraction import parse_simulation_payload
from bridge.simulation_repository import load_states_as_of, store_state
from bridge.simulation_service import SimulationService
from bridge.simulation_snapshot import restore_simulation_snapshot, snapshot_simulation_state
from bridge.sqlite_store import write_transaction


@pytest.mark.parametrize(
    "speaker,members,visible",
    [
        ("Maya", ["Maya.png"], True),
        ("Alice", ["Alice.png", "Maya.png"], False),
        ("Maya", ["Maya.png", "missing.png"], False),
    ],
)
def test_private_tracker_requires_every_resolved_group_reader_to_know_it(
    session_db, monkeypatch, speaker, members, visible
):
    from bridge.group_core import group_state, save_group_state
    from bridge.memory_scope_runtime import resolve_session_memory_scope
    from bridge.simulation_context import story_simulation_context

    config, db, session = session_db
    source = _assistant_row(db)
    SimulationService().apply_payload(
        db,
        "chat",
        "s1",
        {"agendas": [{"npc": "Maya", "objective": "Secretly steal from Alice", "max_steps": 5}]},
        source_rowid=source,
    )
    save_group_state(db, "chat", "s1", {"enabled": True, "mode": "autonomous", "members": members})
    monkeypatch.setattr(
        "bridge.memory_scope_runtime.card_fields_from_file",
        lambda path, **kwargs: {"name": {"Alice.png": "Alice", "Maya.png": "Maya"}.get(str(path), "")},
    )
    scope = resolve_session_memory_scope(
        db,
        "chat",
        session,
        {"name": speaker},
        app_settings=config,
        load_group_state=group_state,
    )
    assert scope is not None
    context = story_simulation_context(db, "chat", "s1", scope)
    assert ("Secretly steal from Alice" in context) is visible


def test_delayed_alias_adopts_prior_scores_and_agenda_with_historical_rollback(session_db):
    _, db, _ = session_db
    service = SimulationService()
    first = _assistant_row(db)
    service.apply_payload(
        db,
        "chat",
        "s1",
        {
            "relationships": [{"npc": "Maya", "sparks_delta": 2}],
            "agendas": [{"npc": "Maya", "objective": "Research", "step": 2, "max_steps": 5}],
        },
        source_rowid=first,
    )
    before = {kind: service.state(db, "chat", "s1", kind, "maya") for kind in ("relationship", "agenda")}
    second = _assistant_row(db)
    entity = npc(db, second)
    service.apply_payload(
        db, "chat", "s1", {"relationships": [{"npc": "Maya", "sparks_delta": 1}]}, source_rowid=second
    )
    assert service.state(db, "chat", "s1", "relationship", "maya") is None
    assert service.state(db, "chat", "s1", "relationship", "maya torres")["sparks"] == 3
    assert service.state(db, "chat", "s1", "agenda", "maya") is None
    assert service.state(db, "chat", "s1", "agenda", "maya torres")["step"] == 3
    context = service.context_for_prompt(db, "chat", "s1")
    assert context.count("REL ") == context.count("AGENDA ") == 1
    assert "3/5" in load_npc_fields(db, entity.npc_id)["agenda"].value
    for kind in before:
        assert service.state(db, "chat", "s1", kind, "maya", through_rowid=first) == before[kind]
        assert service.state(db, "chat", "s1", kind, "maya torres", through_rowid=first) is None
    service.rollback_from_row(db, "chat", "s1", second)
    for kind in before:
        assert service.state(db, "chat", "s1", kind, "maya") == before[kind]
        assert service.state(db, "chat", "s1", kind, "maya torres") is None


def test_conflicting_alias_state_rejects_complete_worker_publication(session_db):
    _, db, _ = session_db
    first = _assistant_row(db)
    refresh(
        session_db,
        lambda *a, **k: json.dumps(
            {
                "npcs": [],
                "simulation": {
                    "relationships": [{"npc": "Maya", "sparks_delta": 2}, {"npc": "Maya Torres", "sparks_delta": 1}]
                },
            }
        ),
    )
    second = _assistant_row(db)
    refresh(
        session_db,
        lambda *a, **k: json.dumps(
            {
                "npcs": [
                    {
                        "name": "Maya Torres",
                        "aliases": ["Maya"],
                        "operations": [
                            {"field": "role", "op": "set", "value": "Guard", "mode": "mutable", "visibility": "shared"}
                        ],
                    }
                ],
                "simulation": {},
            }
        ),
    )
    assert get_npc_extraction_coverage(db, "chat", "s1") == first
    assert list_npc_entities(db, "chat", "s1") == []
    assert db.execute("SELECT source_rowid FROM simulation_sources").fetchall() == [(first,)]
    assert second > first


def test_identical_alias_values_coalesce_and_native_manual_field_remains_owned(session_db):
    _, db, _ = session_db
    first = _assistant_row(db)
    service = SimulationService()
    service.apply_payload(
        db,
        "chat",
        "s1",
        {"relationships": [{"npc": "Maya", "sparks_delta": 2}, {"npc": "Maya Torres", "sparks_delta": 2}]},
        source_rowid=first,
    )
    second = _assistant_row(db)
    NpcService().apply_group(
        db,
        "chat",
        "s1",
        _group(aliases=["Maya"], operations=[_op("relationship", "Manual attitude")]),
        source_rowid=second,
        primary_name="Alice",
        user_name="User",
    )
    entity = list_npc_entities(db, "chat", "s1")[0]
    field = load_npc_fields(db, entity.npc_id)["relationship"]
    service.apply_payload(db, "chat", "s1", {}, source_rowid=second)
    assert service.state(db, "chat", "s1", "relationship", "maya") is None
    assert service.state(db, "chat", "s1", "relationship", "maya torres")["sparks"] == 2
    assert load_npc_fields(db, entity.npc_id)["relationship"] == field


def test_historical_tombstones_do_not_hide_live_entities_at_the_domain_bound(session_db):
    _, db, _ = session_db
    source = _assistant_row(db)
    with write_transaction(db):
        for number in range(400):
            name = f"former-{number:03}"
            store_state(db, "chat", "s1", "quest", name, {"objective": "Old"}, source_rowid=source, now=1)
            store_state(db, "chat", "s1", "quest", name, None, source_rowid=source, now=1)
        for number in range(64):
            store_state(
                db, "chat", "s1", "quest", f"live-{number:03}", {"objective": "Live"}, source_rowid=source, now=1
            )
    states = load_states_as_of(db, "chat", "s1", through_rowid=source)
    assert len(states) == 64 and all(name.startswith("live-") for _, name, _, _ in states)


def test_backfill_does_not_adopt_or_project_an_identity_established_in_the_future(session_db):
    _, db, _ = session_db
    first = _assistant_row(db)
    second = _assistant_row(db)
    entity = npc(db, second)
    service = SimulationService()
    service.apply_payload(db, "chat", "s1", {"relationships": [{"npc": "Maya", "sparks_delta": 2}]}, source_rowid=first)
    assert service.state(db, "chat", "s1", "relationship", "maya")["sparks"] == 2
    assert service.state(db, "chat", "s1", "relationship", "maya torres") is None
    assert "relationship" not in load_npc_fields(db, entity.npc_id)
    service.apply_payload(db, "chat", "s1", {}, source_rowid=second)
    assert service.state(db, "chat", "s1", "relationship", "maya") is None
    assert service.state(db, "chat", "s1", "relationship", "maya torres")["sparks"] == 2


TRACKERS = "TRACKER numerical state. " * 250


@pytest.mark.parametrize("fixed_overflow", [False, True])
def test_director_compacts_trackers_and_preserves_required_revision_context(directed, monkeypatch, fixed_overflow):
    settings, db, session = directed
    monkeypatch.setattr("bridge.director_prompt.simulation_context_for_prompt", lambda *a, **k: TRACKERS)
    data, *_ = build_director_input(
        db,
        "chat",
        session,
        load_narrative_state(db, "chat", "s1"),
        load_session_narrative_settings(db, "chat", "s1"),
        load_director_state(db, "chat", "s1"),
        app_settings=settings,
        valid_characters={"Mara", "Governor"},
        user_characters={"Alex"},
    )
    data.pop("simulation_state")
    data["reassessment_reason"] = "manual"
    core = [
        {"role": "system", "content": DIRECTOR_INSTRUCTION},
        {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
    ]
    settings = replace(settings, context_window_tokens=estimate_message_tokens(core) + 256 + 2000 + 512)
    if fixed_overflow:
        session = session | {"system_prompt": "Required system instruction. " * 150}
    before = load_narrative_clock(db, "chat", "s1")
    calls = []

    def generate(_key, model, messages, **kwargs):
        calls.append(1)
        assert not fixed_overflow, "Fixed context overflow must stop before dispatch"
        profile = context_profile(model, app_settings=settings, requested_output_tokens=2000)
        assert estimate_message_tokens(messages) <= profile.input_budget_tokens
        assert messages[0]["content"] == DIRECTOR_INSTRUCTION
        restored, end = json.JSONDecoder().raw_decode(messages[-1]["content"])
        assert restored == data
        assert len(messages[-1]["content"][end:]) < len(TRACKERS)
        assert all("_context_optional" not in message for message in messages)
        return response(db)

    result = reassess((settings, db, session), generate)
    assert result.result == ("rejected" if fixed_overflow else "accepted")
    assert len(calls) == (0 if fixed_overflow else 1)
    assert load_narrative_clock(db, "chat", "s1") == before


@pytest.mark.parametrize("strategy,expected", [("a", "story::test"), ("b", "utility::test"), ("c", "story::test")])
def test_separate_choices_compact_trackers_with_the_selected_model_budget(novel_db, monkeypatch, strategy, expected):
    from bridge.conversation_lifecycle import configure_conversation, conversation_state, mark_started
    from bridge.light_novel_service import attach_turn, ensure_choices, prepare_turn
    from bridge.model_selection import set_task_model

    db, session, settings = novel_db
    settings = replace(settings, context_window_tokens=3600)
    session = session | {"system_prompt": "FIXED_SENTINEL " * 90}
    configure_conversation(db, "chat", "story", "lightnovel", strategy)
    mark_started(db, "chat", "story", conversation_state(db, "chat", "story").epoch)
    set_task_model(db, "chat", "story", "utility::test")
    record = prepare_turn(db, "chat", session, "opening:1", "owner", rng=lambda _: 3)
    attach_turn(db, record, story_row(db), "The door opens.")
    monkeypatch.setattr("bridge.light_novel_service.story_simulation_context", lambda *a, **k: TRACKERS)
    calls = []

    def generate(_key, model, messages, **kwargs):
        calls.append(1)
        assert not db.in_transaction and model == expected
        profile = context_profile(model, app_settings=settings, requested_output_tokens=1200)
        assert estimate_message_tokens(messages) <= profile.input_budget_tokens
        assert "Generate exactly 3 distinct next choices" in messages[0]["content"]
        assert "Return only JSON" in messages[0]["content"]
        fixed, end = json.JSONDecoder().raw_decode(messages[-1]["content"])
        assert fixed["current_story"] == "The door opens."
        assert "FIXED_SENTINEL" in fixed["system_prompt"]
        assert "narrative_policy" in fixed and "user_persona" in fixed
        assert "simulation_state" not in fixed
        assert len(messages[-1]["content"][end:]) < len(TRACKERS)
        assert all("_context_optional" not in message for message in messages)
        return json.dumps({"choices": ["Go inside", "Wait outside", "Look around"]})

    result = ensure_choices(
        db, record.nonce, session, {"name": "Alice"}, provider_port=ProviderPort(generate), app_settings=settings
    )
    assert calls == [1] and result.generation_status == "ready"


def test_restored_history_keeps_intermediate_modifiers_and_suffix_rollback(session_db):
    config, db, _ = session_db
    service = SimulationService()
    old_key = {"actor": {"inventory_add": [{"name": "Old key", "domain": "lock", "modifier": 1}]}}
    first = _assistant_row(db, "The user receives an old key.")
    service.apply_payload(db, "chat", "s1", old_key, source_rowid=first)
    with write_transaction(db):
        middle = db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('chat','s1','user','Go north',2)"
        ).lastrowid
    second = _assistant_row(db, "The user finds a map.")
    service.apply_payload(
        db,
        "chat",
        "s1",
        {"actor": {"inventory_add": [{"name": "Map", "domain": "lock", "modifier": 2}]}},
        source_rowid=second,
    )
    snapshot = snapshot_simulation_state(db, "chat", "s1", second)
    create_session(db, "chat", "dummy::model", session_id="branch", app_settings=config)
    rowids = {0: 0}
    with write_transaction(db):
        for source in (first, middle, second):
            rowids[source] = db.execute(
                "INSERT INTO messages(chat_id,session_id,role,content,created_at) "
                "SELECT chat_id,'branch',role,content,created_at FROM messages WHERE id=?",
                (source,),
            ).lastrowid
        restore_simulation_snapshot(db, "chat", "branch", remap_checkpoint(snapshot, rowids))
    origin = service.state(db, "chat", "s1", "actor", "user")
    assert service.state(db, "chat", "branch", "actor", "user") == origin
    historical = service.state(db, "chat", "s1", "actor", "user", through_rowid=middle)
    assert service.state(db, "chat", "branch", "actor", "user", through_rowid=rowids[middle]) == historical
    assert service.actor_modifier(db, "chat", "branch", "lock", through_rowid=rowids[middle]) == 1
    service.rollback_from_row(db, "chat", "branch", rowids[second])
    service.apply_payload(db, "chat", "branch", old_key, source_rowid=rowids[first])
    assert service.state(db, "chat", "branch", "actor", "user") == historical
    assert service.state(db, "chat", "s1", "actor", "user") == origin
    assert db.execute("SELECT source_rowid FROM simulation_sources WHERE session_id='branch'").fetchall() == [
        (rowids[first],)
    ]


def test_checkpoint_rejects_large_history_even_when_current_state_fits(session_db):
    from bridge.simulation_repository import store_state

    _, db, _ = session_db
    source = _assistant_row(db)
    with write_transaction(db):
        for revision in range(90):
            store_state(
                db,
                "chat",
                "s1",
                "quest",
                "long-lived",
                {"revision": revision, "objective": "é" * 7000},
                source_rowid=source,
                now=revision,
            )
    with pytest.raises(ValueError, match="checkpoint"):
        snapshot_simulation_state(db, "chat", "s1", source)


def test_alias_tombstone_restores_with_its_historical_identity_transition(session_db):
    from test_simulation_projection import npc

    config, db, _ = session_db
    service = SimulationService()
    first = _assistant_row(db)
    service.apply_payload(db, "chat", "s1", {"relationships": [{"npc": "Maya", "sparks_delta": 2}]}, source_rowid=first)
    second = _assistant_row(db)
    npc(db, second)
    service.apply_payload(db, "chat", "s1", {}, source_rowid=second)
    snapshot = snapshot_simulation_state(db, "chat", "s1", second)
    create_session(db, "chat", "dummy::model", session_id="branch", app_settings=config)
    rowids = {0: 0}
    with write_transaction(db):
        for source in (first, second):
            rowids[source] = db.execute(
                "INSERT INTO messages(chat_id,session_id,role,content,created_at) "
                "SELECT chat_id,'branch',role,content,created_at FROM messages WHERE id=?",
                (source,),
            ).lastrowid
        restore_simulation_snapshot(db, "chat", "branch", remap_checkpoint(snapshot, rowids))
    assert service.state(db, "chat", "branch", "relationship", "maya") is None
    assert service.state(db, "chat", "branch", "relationship", "maya torres")["sparks"] == 2
    assert service.state(db, "chat", "branch", "relationship", "maya", through_rowid=rowids[first])["sparks"] == 2
    service.rollback_from_row(db, "chat", "branch", rowids[second])
    assert service.state(db, "chat", "branch", "relationship", "maya")["sparks"] == 2
    assert service.state(db, "chat", "branch", "relationship", "maya torres") is None


@pytest.mark.parametrize(
    "payload",
    [
        {"quests": [{"id": "gate", "objective": {"nested": "untrusted"}}]},
        {"agendas": [{"npc": "Maya", "max_steps": True}]},
        {"agendas": [{"npc": "Maya", "step": "2"}]},
        {"agendas": [{"npc": "Maya", "complete": None}]},
        {"relationships": [{"npc": "Maya", "sparks_delta": 1.5}]},
        {"relationships": [{"npc": "Maya", "grudge_delta": False}]},
        {"relationships": [{"npc": "Maya", "apology": None}]},
        {"relationships": [{"npc": ["Maya"]}]},
        {"relationships": [{}]},
        {"relationships": [None]},
        {"relationships": None},
        {"actor": False},
        {"actor": None},
        {"actor": {"inventory_remove": None}},
        {"actor": {"inventory_add": [{"name": "Key", "modifier": True}]}},
        {"actor": {"skills_add": [{"name": "Climb", "domain": []}]}},
        {"actor": {"conditions_add": [{"name": None}]}},
        {"actor": {"inventory_add": [""]}},
        {"on_screen_npcs": [123]},
        {"factions": [{"name": "Guard", "lies": [123]}]},
        {"factions": [{"name": "Guard", "relations": {"Guild": {"hostile": True}}}]},
        {"foreshadowing": [{"id": "key", "seed": None}]},
        {"quests": [{"id": "gate", "progress_current": False}]},
    ],
)
def test_supplied_fields_require_their_declared_json_types(payload):
    assert parse_simulation_payload(json.dumps({"npcs": [], "simulation": payload})) == ({}, False)


def test_malformed_typed_part_preserves_existing_state_npcs_and_worker_coverage(session_db):
    _, db, _ = session_db
    first = _assistant_row(db)
    refresh(
        session_db,
        lambda *a, **k: json.dumps(
            {
                "npcs": [],
                "simulation": {
                    "quests": [{"id": "gate", "objective": "Find the brass key"}],
                    "agendas": [{"npc": "Maya", "objective": "Research", "step": 2, "max_steps": 5}],
                },
            }
        ),
    )
    service = SimulationService()
    before = {
        kind: service.state(db, "chat", "s1", kind, name) for kind, name in (("quest", "gate"), ("agenda", "maya"))
    }
    second = _assistant_row(db, "Maya gives a new clue.")
    refresh(
        session_db,
        lambda *a, **k: json.dumps(
            {
                "npcs": [
                    {
                        "name": "New witness",
                        "aliases": [],
                        "operations": [{"field": "role", "op": "set", "value": "Witness", "mode": "mutable"}],
                    }
                ],
                "simulation": {
                    "quests": [{"id": "gate", "objective": {"nested": "untrusted"}}],
                    "agendas": [{"npc": "Maya", "max_steps": True}],
                },
            }
        ),
    )
    assert get_npc_extraction_coverage(db, "chat", "s1") == first
    assert list_npc_entities(db, "chat", "s1") == []
    assert service.state(db, "chat", "s1", "quest", "gate") == before["quest"]
    assert service.state(db, "chat", "s1", "agenda", "maya") == before["agenda"]
    assert db.execute("SELECT source_rowid FROM simulation_sources ORDER BY source_rowid").fetchall() == [(first,)]
    assert second > first
