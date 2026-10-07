"""Marker-free synthetic packets driving the real checkpoint and ending services."""

from __future__ import annotations

import json
from unittest.mock import patch

from story_memory_eval_support import CHAT, OfflineTransport
from story_memory_retrieval_fixture import require

PRELUDE = ("Prepare the final entries of the harbor record.", "The harbor record is ready for its final entries.")
RESOLUTION = ("Close the original harbor record.", "The original harbor record is complete.")
EPILOGUE = "Rowan closes the harbor ledger."


def narrative_packet(stage: str, resolution_rowid: int = 0) -> dict:
    packet = {
        "schema_version": 1,
        "story_phase": "escalation",
        "scene": {
            "scene_id": "harbor_record_scene",
            "thread_id": "harbor_record",
            "viewpoint_character": "Rowan",
            "pov_mode": "third_person_rotating",
            "user_present": False,
            "purpose": PRELUDE[0],
            "transition_type": "continue",
        },
        "threads": [
            {"thread_id": "harbor_record", "title": "Harbor record", "status": "active", "summary": PRELUDE[1]}
        ],
        "arcs": [
            {
                "arc_id": "harbor_record_arc",
                "title": "Complete the harbor record",
                "status": "active",
                "phase": "development",
                "importance": "major",
                "summary": "The harbor record is being assembled.",
                "open_questions": ["Is the original harbor record complete?"],
                "related_threads": ["harbor_record"],
                "evidence": [],
            }
        ],
    }
    if stage == "resolution":
        require(resolution_rowid > 0, "Resolution needs a committed assistant identity")
        evidence = [{"rowid": resolution_rowid, "quote": RESOLUTION[1]}]
        packet["story_phase"] = "resolution"
        packet["scene"]["purpose"] = "Complete the original harbor record."
        packet["threads"][0].update(status="resolved", summary=RESOLUTION[1])
        packet["arcs"][0].update(
            status="resolved", phase="resolution", summary=RESOLUTION[1], open_questions=[], evidence=evidence
        )
        packet["resolution"] = {"resolved": True, "evidence": evidence}
    elif stage == "epilogue":
        packet["story_phase"] = "epilogue"
        packet["scene"]["purpose"] = "Close the harbor record."
        packet["threads"][0].update(status="resolved", summary=EPILOGUE)
        packet["arcs"] = []
    else:
        require(stage == "ready", "Unexpected narrative publication stage")
    return packet


class PanelTransport(OfflineTransport):
    """Only explicitly consumed synthetic purposes are supported."""

    def __init__(self):
        super().__init__({})
        self.stage = "unpublished"
        self.resolution_rowid = 0

    def generate(self, api_key, model, messages, **kwargs):
        sid = kwargs.get("session_id", "")
        if sid.startswith("episodic:") or (sid.startswith("telegram:") and sid.count(":") == 2):
            return super().generate(api_key, model, messages, **kwargs)
        self.record("provider", {"model": model, "purpose": sid})
        if sid.endswith(":epilogue-brief"):
            payload = json.loads(messages[-1]["content"])
            require(payload["pov"] == "third_person_rotating", "Unexpected epilogue POV")
            return json.dumps(
                {
                    "schema_version": 1,
                    "expected_revision": payload["expected_revision"],
                    "time_scope": "Immediate aftermath of the completed harbor record.",
                    "cover": ["The completed harbor record"],
                    "do_not_invent": ["New survey facts", "Changes to recorded ending facts"],
                    "pov": payload["pov"],
                }
            )
        if sid.startswith("narrative:"):
            stage, rowid = self.stage, self.resolution_rowid
            # The production finale hook may reconcile before accept_turn returns.
            # Bind this packet to its supplied committed assistant row, never a
            # predicted ID or a caller-side stage update after acceptance.
            if stage == "ready":
                from bridge.roleplay_format import normalize_roleplay_transport
                from bridge.telegram_output import telegram_transport_output

                rows = json.loads(messages[-1]["content"].split("Complete committed transcript rows:\n", 1)[1])
                expected = normalize_roleplay_transport(telegram_transport_output(RESOLUTION[1]))
                if rows and rows[-1]["role"] == "assistant" and rows[-1]["content"] == expected:
                    rowid = rows[-1]["rowid"]
                    require(type(rowid) is int and rowid > 0, "Resolution needs a committed assistant identity")
                    stage = "resolution"
            return json.dumps(narrative_packet(stage, rowid))
        if sid.endswith(":epilogue"):
            self.stage = "epilogue"
            return EPILOGUE
        raise RuntimeError("Unexpected synthetic provider purpose")


def prepare_checkpoint(corpus):
    from bridge.ending_service import (
        load_ending_state,
        mark_finale_ready,
        require_current_ending_facts,
        set_ending_goal,
    )
    from bridge.narrative_checkpoints import create_pre_finale_checkpoint_and_enter
    from bridge.narrative_repository import load_narrative_clock
    from bridge.narrative_settings import (
        normalize_narrative_settings,
        preset_narrative_settings,
        save_session_narrative_settings,
    )

    runtime, db = corpus.runtime, corpus.db
    policy = preset_narrative_settings("world_driven").to_dict() | {"preset": "custom", "ending_mode": "closed_story"}
    save_session_narrative_settings(db, CHAT, "main", normalize_narrative_settings(policy))
    corpus.accept("closure.prelude", *PRELUDE)
    runtime.transport.stage = "ready"
    corpus.reconcile("closure.reconcile_ready", corpus.events["closure.prelude.assistant"]["row_id"])
    clock = load_narrative_clock(db, CHAT, "main")
    require_current_ending_facts(clock)
    ending = load_ending_state(db, CHAT, "main")
    set_ending_goal(
        db,
        CHAT,
        "main",
        "Complete the harbor record.",
        source="user",
        story_revision=clock["state_revision"],
        expected_lifecycle_revision=ending.lifecycle_revision,
        expected_goal_revision=ending.goal_revision,
    )
    ending = load_ending_state(db, CHAT, "main")
    clock = load_narrative_clock(db, CHAT, "main")
    mark_finale_ready(
        db,
        CHAT,
        "main",
        story_revision=clock["state_revision"],
        expected_lifecycle_revision=ending.lifecycle_revision,
        expected_goal_revision=ending.goal_revision,
        direction_revision=1,
        reason=PRELUDE[1],
    )
    ending = load_ending_state(db, CHAT, "main")
    clock = load_narrative_clock(db, CHAT, "main")
    checkpoint = create_pre_finale_checkpoint_and_enter(
        db,
        CHAT,
        "main",
        expected_story_revision=clock["state_revision"],
        expected_lifecycle_revision=ending.lifecycle_revision,
        operation_id="retrieval-panel-v1-finale",
    )
    require(checkpoint.payload["ending"]["required_arcs"] == ["harbor_record_arc"], "Checkpoint arc contract changed")
    require(len(checkpoint.payload["memory"]["episodic"]) == 16, "Checkpoint corpus count changed")
    corpus.events["closure.checkpoint"] = {
        "checkpoint_id": checkpoint.checkpoint_id,
        "through_rowid": checkpoint.through_rowid,
    }
    return checkpoint


def close_original(corpus):
    from bridge import epilogue_service
    from bridge.delivery_port import DeliveryPort
    from bridge.delivery_progress import checkpoint, prepare_progress
    from bridge.ending_service import load_ending_state

    runtime, db = corpus.runtime, corpus.db
    corpus.accept("closure.resolution", *RESOLUTION)
    rowid = corpus.events["closure.resolution.assistant"]["row_id"]
    runtime.transport.stage, runtime.transport.resolution_rowid = "resolution", rowid
    corpus.reconcile("closure.reconcile_resolution", rowid)
    ending = load_ending_state(db, CHAT, "main")
    require(
        ending.lifecycle == "resolution_committed" and ending.resolution_rowid == rowid,
        "Resolution did not commit actual assistant evidence",
    )

    def deliver(token, chat_id, text, connection=None, session_id=None, assistant_rowid=None, **kwargs):
        require(connection is db and chat_id == CHAT and not db.in_transaction, "Unexpected delivery boundary")
        payload, ids, complete, source = prepare_progress(db, assistant_rowid, text)
        if not complete:
            checkpoint(
                db, assistant_rowid, [*ids, 700], complete=True, expected_source=source, expected_payload=payload
            )

    delivery = DeliveryPort(
        runtime.unexpected, runtime.unexpected, deliver, runtime.unexpected, runtime.unexpected, runtime.unexpected
    )
    with patch.object(epilogue_service, "card_fields_from_file", lambda *args, **kwargs: runtime.fields):
        result = epilogue_service.complete_epilogue(
            db,
            "",
            "",
            CHAT,
            corpus.sessions["main"],
            provider_port=runtime.provider,
            delivery_port=delivery,
            persona_service=runtime.persona,
            app_settings=runtime.settings,
            manual=True,
        )
    require(result.completed and result.delivered and result.lifecycle == "closed", "Production epilogue failed")
    ending = load_ending_state(db, CHAT, "main")
    corpus.record_row("closure.epilogue.assistant", "main", ending.epilogue_committed_rowid, "assistant", EPILOGUE)
    runtime.drain("episodes")
    return result
