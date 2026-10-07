"""Publish only complete canonical row outcomes inside the caller's transaction."""

import json
import time

from bridge.memory_artifact_store import store_artifact_visibility
from bridge.memory_store import pending_memory_invalidation
from bridge.npc_repository import set_npc_extraction_coverage
from bridge.npc_service import NpcService
from bridge.npc_types import NpcExtractionGroup, NpcOperation
from bridge.simulation_narrative import canonicalize_narrative_links
from bridge.simulation_projection import is_managed_field
from bridge.simulation_service import SimulationService


def publish_derived(db, chat_id, session_id, layer, payload, through):
    if layer == "summary":
        text = "\n".join(block["text"] for block in payload["blocks"])
        db.execute(
            "INSERT OR REPLACE INTO session_summaries VALUES(?,?,?,?,?)",
            (chat_id, session_id, text, through, time.time()),
        )
        store_artifact_visibility(db, chat_id, session_id, "summary", payload["blocks"])
    elif layer == "scene":
        db.execute(
            "INSERT OR REPLACE INTO scene_states VALUES(?,?,?,?,?)",
            (
                chat_id,
                session_id,
                json.dumps(payload["state"], ensure_ascii=False, sort_keys=True),
                through,
                time.time(),
            ),
        )
        store_artifact_visibility(db, chat_id, session_id, "scene", payload["blocks"])
    elif layer == "curator":
        db.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
            (
                f"memory_curator:{chat_id}:{session_id}",
                json.dumps(
                    {
                        "items": payload.get("memories", []),
                        "through_rowid": through,
                        "updated_at": time.time(),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            ),
        )
    elif layer == "npc":
        service = NpcService()
        for item in payload.get("npcs", []):
            operations = tuple(
                NpcOperation(op["field"], op["op"], op["value"], op["mode"], op["visibility"], tuple(op["known_by"]))
                for op in item["operations"]
                if not is_managed_field(db, chat_id, session_id, item["name"], op["field"])
            )
            group = NpcExtractionGroup(item["name"], tuple(item["aliases"]), operations)
            service.apply_group(
                db,
                chat_id,
                session_id,
                group,
                source_rowid=through,
                primary_name=payload["primary_name"],
                user_name=payload["user_name"],
            )
        simulation = canonicalize_narrative_links(
            db, chat_id, session_id, payload.get("simulation") or {}, through
        )
        SimulationService().apply_payload(
            db,
            chat_id,
            session_id,
            simulation,
            source_rowid=through,
            primary_name=payload.get("primary_name", ""),
            user_name=payload.get("user_name", ""),
        )
        set_npc_extraction_coverage(db, chat_id, session_id, through, time.time())
        db.execute(
            "UPDATE memory_layer_state SET invalidated_from_id=MAX(invalidated_from_id,?) "
            "WHERE chat_id=? AND session_id=? AND layer='npc' AND invalidated_from_id IS NOT NULL",
            (through + 1, chat_id, session_id),
        )
    else:
        raise ValueError("Unknown native derived layer")


def restore_derived(db, chat_id, session_id, layer, payload, through):
    if layer == "npc":
        # Missing accumulator proof requires source replay, not retirement of
        # accepted field history from an unchanged canonical prefix.
        invalidated = pending_memory_invalidation(db, chat_id, session_id, "npc")
        if invalidated is not None:
            NpcService().rollback_from_row(db, chat_id, session_id, invalidated)
            SimulationService().rollback_from_row(db, chat_id, session_id, invalidated)
        set_npc_extraction_coverage(db, chat_id, session_id, through, time.time())
    elif payload:
        publish_derived(db, chat_id, session_id, layer, payload, through)
    elif layer in {"summary", "scene"}:
        # Legacy panel contents can remain visible in status, but incomplete
        # or opaque legacy coverage never becomes classified prompt authority.
        db.execute(
            "DELETE FROM memory_artifact_visibility WHERE chat_id=? AND session_id=? AND artifact_kind=?",
            (chat_id, session_id, layer),
        )
