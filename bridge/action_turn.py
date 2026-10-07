"""Bind automatic adjudication to the existing durable generation job boundary."""

from __future__ import annotations

import json

from bridge.action_adjudication import ActionTurn, prepare_action_turn
from bridge.conversation_lifecycle import conversation_state, is_group_conversation
from bridge.light_novel_format import NEXT_SCENE_INSTRUCTION


def begin_action_for_job(
    db,
    chat_id,
    session,
    text,
    operation_id,
    *,
    provider_port,
    app_settings,
    actor_id="",
    group_turn=None,
    through_rowid=None,
    telegram_message_id=None,
):
    # Ad-hoc/internal generation has no restart-safe identity. Never create a roll for it.
    if group_turn or is_group_conversation(db, chat_id, session["session_id"]):
        return ActionTurn()
    if operation_id is not None:
        job = db.execute(
            "SELECT chat_id,session_id,payload_json,telegram_message_id,job_id FROM jobs WHERE CAST(job_id AS TEXT)=?",
            (str(operation_id),),
        ).fetchone()
    else:
        job = db.execute(
            "SELECT chat_id,session_id,payload_json,telegram_message_id,job_id FROM jobs "
            "WHERE chat_id=? AND session_id=? AND telegram_message_id=? "
            "AND kind IN ('generation','message','image','voice','document') ORDER BY job_id DESC LIMIT 1",
            (chat_id, session["session_id"], str(telegram_message_id)),
        ).fetchone()
    if job is None:
        return ActionTurn()
    payload = json.loads(job[2])
    durable_actor = str(payload.get("actor_id") or "")
    if job[:2] != (chat_id, session["session_id"]) or (actor_id and durable_actor != actor_id):
        raise ValueError("Action job belongs to another session or actor")
    epoch = payload.get("epoch")
    if epoch is not None and epoch != conversation_state(db, chat_id, session["session_id"]).epoch:
        raise ValueError("Action job belongs to an earlier conversation")
    if (
        text == NEXT_SCENE_INSTRUCTION
        or payload.get("narrative_input") == "steering"
        or payload.get("scene_transition") is True
    ):
        return ActionTurn()
    return prepare_action_turn(
        db,
        chat_id,
        session,
        text,
        f"telegram:{job[3]}" if job[3] not in (None, "") else f"job:{job[4]}",
        provider_port=provider_port,
        app_settings=app_settings,
        actor_id=durable_actor,
        through_rowid=through_rowid,
    )
