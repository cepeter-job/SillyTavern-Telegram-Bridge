"""Real-state regressions from whole-branch tracker review."""

import pytest
from test_memory_completion_safety import session_db as session_db
from test_simulation_trackers import _assistant_row

from bridge.simulation_service import SimulationService


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
