from contextlib import closing

import pytest
from miniapp_test_support import identity, make_services


def setup(tmp_path):
    from bridge.miniapp_context import current_session

    services = make_services(tmp_path)
    who = identity()
    session = current_session(services, who, {})["session"]
    return services, who, {"session_id": session["session_id"]}, session


def _apply(services, who, session, rowid, value, *, field="relationship", visibility="shared", known_by=()):
    from bridge.npc_service import NpcService
    from bridge.npc_types import NpcExtractionGroup, NpcOperation

    with closing(services.db_factory()) as db, db:
        result = NpcService().apply_group(
            db,
            who.chat_id,
            session["session_id"],
            NpcExtractionGroup(
                "Maya Torres",
                ("Maya",),
                (
                    NpcOperation(
                        field,
                        "set",
                        value,
                        "mutable",
                        visibility,
                        tuple(known_by),
                    ),
                ),
            ),
            source_rowid=rowid,
            primary_name="Default",
            user_name="User",
        )
    assert result.applied == 1
    return result.npc_id


def test_npc_list_search_detail_history_and_private_visibility(tmp_path):
    from bridge.miniapp_npc import npc_detail, npc_history, npcs

    services, who, _params, session = setup(tmp_path)
    npc_id = _apply(services, who, session, 10, "Archivist", field="role")
    _apply(
        services,
        who,
        session,
        11,
        ["Vault code 7741"],
        field="secrets",
        visibility="restricted",
        known_by=("Maya Torres",),
    )

    listing = npcs(services, who, {"q": "maya"})
    assert listing["total"] == 1
    assert listing["npcs"][0]["npc_id"] == npc_id
    assert listing["npcs"][0]["name"] == "Maya Torres"
    assert npcs(services, who, {"q": "unknown"})["npcs"] == []
    assert npcs(services, identity("67890"), {})["npcs"] == []

    detail = npc_detail(services, who, {"npc_id": str(npc_id)})
    assert detail["npc"]["name"] == "Maya Torres"
    assert detail["fields"]["role"]["value"] == "Archivist"
    assert "secrets" not in detail["fields"]

    history = npc_history(services, who, {"npc_id": str(npc_id)})
    assert any(item["field"] == "role" for item in history["history"])
    assert all(item["field"] != "secrets" for item in history["history"])


def test_npc_undo_requires_confirmation_and_rejects_stale_change(tmp_path):
    from bridge.miniapp_errors import MiniAppError
    from bridge.miniapp_npc import npc_detail, undo_npc_field

    services, who, params, session = setup(tmp_path)
    npc_id = _apply(services, who, session, 10, "cautious")
    _apply(services, who, session, 20, "hostile")
    current = npc_detail(services, who, {"npc_id": str(npc_id)})
    latest = current["fields"]["relationship"]["change_id"]

    with pytest.raises(MiniAppError) as confirm:
        undo_npc_field(
            services,
            who,
            {**params, "npc_id": str(npc_id), "field": "relationship", "change_id": latest},
        )
    assert confirm.value.code == "confirmation"

    with pytest.raises(MiniAppError) as stale:
        undo_npc_field(
            services,
            who,
            {
                **params,
                "npc_id": str(npc_id),
                "field": "relationship",
                "change_id": latest - 1,
                "confirm": True,
            },
        )
    assert stale.value.code == "stale"

    result = undo_npc_field(
        services,
        who,
        {
            **params,
            "npc_id": str(npc_id),
            "field": "relationship",
            "change_id": latest,
            "confirm": True,
        },
    )
    assert result["restored"] is True
    assert npc_detail(services, who, {"npc_id": str(npc_id)})["fields"]["relationship"]["value"] == "cautious"


def test_npc_refresh_is_a_background_miniapp_route():
    from bridge.miniapp_npc import routes

    route = next(route for route in routes() if route.path == "/npcs/refresh")
    assert route.method == "POST"
    assert route.background_kind == "npc_refresh"


def test_npc_history_marks_only_latest_change_per_field_undoable(tmp_path):
    from bridge.miniapp_npc import npc_history

    services, who, _params, session = setup(tmp_path)
    npc_id = _apply(services, who, session, 10, "cautious")
    _apply(services, who, session, 20, "hostile")
    _apply(services, who, session, 30, "Archivist", field="role")

    history = npc_history(services, who, {"npc_id": str(npc_id)})["history"]
    relationship = [item for item in history if item["field"] == "relationship"]
    role = [item for item in history if item["field"] == "role"]

    assert [item["latest_for_field"] for item in relationship] == [False, True]
    assert [item["latest_for_field"] for item in role] == [True]
