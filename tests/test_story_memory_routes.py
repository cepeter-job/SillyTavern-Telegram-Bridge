"""Real generation entrypoints share one accepted boundary with native NPC context."""

from types import SimpleNamespace

import pytest
from application_test_setup import (
    make_test_delivery_port,
    make_test_group_service,
    make_test_persona_service,
    make_test_provider_port,
    make_test_rag_service,
)
from settings_test_support import make_test_settings
from test_story_memory_artifacts import service, write_artifacts
from test_story_memory_scope import accept, append, scope
from test_story_memory_scope import db as db
from test_story_memory_scope import forbid_real_hindsight_client as forbid_real_hindsight_client

from bridge import continuation, edit_messages, image_messages, message_commands, regeneration
from bridge.memory_fact_store import accept_source_facts
from bridge.memory_store import next_source_segment
from bridge.npc_service import NpcService
from bridge.npc_types import NpcExtractionGroup, NpcOperation


class PromptCaptured(Exception):
    pass


@pytest.mark.parametrize("route", ["ordinary", "edit", "regen", "continue", "image"])
def test_actual_generation_paths_forward_scope_scene_and_eligible_past_fact(db, monkeypatch, route):
    first = append(db, "The previous key is silver.")
    accept(db, "The previous key is silver.", audience=(), visibility="shared")
    user = append(db, "Where is the previous key?")
    accept_source_facts(db, next_source_segment(db, "c", "s", "episodes"), [])
    answer = db.execute(
        "INSERT INTO messages(chat_id,session_id,role,content,created_at) "
        "VALUES('c','s','assistant','Discarded previous key answer.',4)"
    ).lastrowid
    db.commit()
    accept(db, "DISCARDED previous key fact.", audience=(), visibility="shared")
    write_artifacts(db, first)
    settings = make_test_settings()
    session = {"session_id": "s", "model_id": "m", "persona_id": "", "world_file": ""}
    fields = {"name": "Bob"}
    captured = {}
    npc_scopes = []

    class Npc:
        def context_for_prompt(self, *args, **kwargs):
            npc_scopes.append(kwargs.get("memory_scope"))
            return ""

    def capture(*args, **kwargs):
        captured.update(kwargs)
        raise PromptCaptured

    owner = {
        "ordinary": message_commands,
        "edit": edit_messages,
        "regen": regeneration,
        "continue": continuation,
        "image": image_messages,
    }[route]
    monkeypatch.setattr(owner, "build_chat_messages", capture)
    if hasattr(owner, "require_started"):
        monkeypatch.setattr(owner, "require_started", lambda *a: True)
    kwargs = dict(
        provider_port=make_test_provider_port(),
        memory_service=service(),
        npc_service=Npc(),
        persona_service=make_test_persona_service(),
        app_settings=settings,
        rag_service=make_test_rag_service(),
    )
    with pytest.raises(PromptCaptured):
        if route == "ordinary":
            message_commands.generate_and_store_reply(
                db,
                "",
                "",
                fields,
                "c",
                "previous key",
                session,
                "s",
                "m",
                None,
                "",
                None,
                None,
                group_service=make_test_group_service(app_settings=settings),
                **kwargs,
            )
        elif route == "edit":
            edit_messages.regenerate_edited_turn(db, "", "", session, fields, "c", user, "previous key", **kwargs)
        elif route == "regen":
            regeneration.regenerate_last(
                db,
                "",
                "",
                session,
                fields,
                "c",
                delivery_port=make_test_delivery_port(),
                **kwargs,
            )
        elif route == "continue":
            continuation.continue_last(
                db,
                "",
                "",
                session,
                fields,
                "c",
                delivery_port=make_test_delivery_port(),
                **kwargs,
            )
        else:
            image_messages.process_image_message(
                db,
                "",
                "",
                session,
                fields,
                "c",
                "previous key",
                b"synthetic",
                group_service=make_test_group_service(app_settings=settings),
                group_director_service=SimpleNamespace(),
                **kwargs,
            )
    assert captured["scene_context"] == "Location: The tower"
    assert "The previous key is silver." in captured["episodic_context"]
    assert ("DISCARDED" in captured["episodic_context"]) is (route not in {"edit", "regen"})
    assert npc_scopes[0] is not None
    assert npc_scopes[0].through_rowid == (first if route == "edit" else user if route == "regen" else answer)


def test_npc_pending_invalidation_fences_suffix_before_worker_recovery(db):
    npc = NpcService()
    first = append(db, "Maya has red hair.")
    second = append(db, "Maya dyes her hair blue.")
    for row, color in [(first, "red"), (second, "blue")]:
        result = npc.apply_group(
            db,
            "c",
            "s",
            NpcExtractionGroup("Maya", (), (NpcOperation("mood", "set", color, "mutable", "shared", ()),)),
            source_rowid=row,
            primary_name="Mira",
            user_name="User",
        )
        assert result.applied == 1
    db.execute("UPDATE messages SET content='Maya leaves her hair unchanged.' WHERE id=?", (second,))
    db.commit()
    text = npc.context_for_prompt(
        db,
        "c",
        {"session_id": "s"},
        {"name": "Mira"},
        "Maya",
        [],
        memory_scope=scope(db),
    )
    assert "red" in text and "blue" not in text


def test_autonomous_scope_resolves_all_actual_character_cards(db, monkeypatch):
    import bridge.memory_scope_runtime as runtime
    from bridge.group_core import group_state, save_group_state
    from bridge.memory_scope_runtime import resolve_session_memory_scope

    append(db)
    accept(db, "The previous key is secret.")
    save_group_state(
        db,
        "c",
        "s",
        {
            "enabled": True,
            "mode": "autonomous",
            "members": ["mira.png", "bob.png"],
            "turn_index": 0,
        },
    )
    monkeypatch.setattr(
        runtime, "card_fields_from_file", lambda path, **kwargs: {"name": {"mira.png": "Mira", "bob.png": "Bob"}[path]}
    )
    resolved = resolve_session_memory_scope(
        db,
        "c",
        {"session_id": "s"},
        {"name": "Mira"},
        app_settings=make_test_settings(),
        load_group_state=group_state,
    )
    assert resolved.principals == ("bob", "mira") and resolved.consumer == "character"
    from bridge.memory_scope_store import read_episodic_block

    assert read_episodic_block(db, resolved, "previous key").text == ""
