"""Existing durable claims drive complete bounded native derived-state extraction."""

from __future__ import annotations

from bridge.memory_draft_publish import publish_derived, restore_derived
from bridge.memory_draft_store import process_draft_parts


def run_derived_layer(db, claim, session, fields, *, provider_port, app_settings, valid, max_parts=8):
    layer, chat_id, session_id = claim.layer, claim.chat_id, claim.session_id

    def extract(previous, source):
        if layer == "summary":
            from bridge.memory import extract_summary_segment

            return extract_summary_segment(
                db, chat_id, session, previous, source, provider_port=provider_port, app_settings=app_settings
            )
        if layer == "scene":
            from bridge.scene_state import extract_scene_segment

            return extract_scene_segment(
                db,
                chat_id,
                session,
                fields.get("name", "Story"),
                previous,
                source,
                provider_port=provider_port,
                app_settings=app_settings,
            )
        if layer == "curator":
            from bridge.memory_curator import extract_curator_segment

            return extract_curator_segment(
                db,
                chat_id,
                session,
                fields.get("name", "Story"),
                previous,
                source,
                provider_port=provider_port,
                app_settings=app_settings,
            )
        if layer == "npc":
            from bridge.npc_extraction import extract_npc_segment

            return extract_npc_segment(
                db, chat_id, session, fields, previous, source, provider_port=provider_port, app_settings=app_settings
            )
        raise ValueError("Unknown native derived layer")

    on_extract_error = None
    if layer == "npc":
        from bridge.npc_extraction import publish_valid_simulation_from_npc_error

        def on_npc_extract_error(error, source):
            publish_valid_simulation_from_npc_error(db, chat_id, session_id, error, source)

        on_extract_error = on_npc_extract_error
    return process_draft_parts(
        db,
        claim,
        extract=extract,
        publish=lambda payload, through: publish_derived(db, chat_id, session_id, layer, payload, through),
        restore=lambda payload, through: restore_derived(db, chat_id, session_id, layer, payload, through),
        valid=valid,
        max_parts=max_parts,
        completed_payload={} if layer == "npc" else None,
        on_extract_error=on_extract_error,
    )
