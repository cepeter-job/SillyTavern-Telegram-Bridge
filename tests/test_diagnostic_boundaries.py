"""Observe real boundary outcomes without retaining arguments or private results."""

import asyncio
import json
import logging
from contextlib import closing
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer
from miniapp_test_support import identity, make_services, signed_data

from bridge.diagnostic_events import diagnostic_context, diagnostic_scope, scope_reference


def events(caplog, prefix):
    return [
        record.diagnostic_fields
        for record in caplog.records
        if getattr(record, "diagnostic_fields", {}).get("event", "").startswith(prefix)
    ]


@pytest.mark.parametrize("fails", [False, True])
def test_reply_boundary_preserves_delivery_result_and_omits_reply(tmp_path, monkeypatch, caplog, fails):
    from settings_test_support import make_test_settings

    import bridge.response_delivery as delivery

    caplog.set_level(logging.INFO, logger="bridge.events")
    failure = RuntimeError("PRIVATE_DELIVERY_ERROR")

    def send(*_args, **_kwargs):
        if fails:
            raise failure
        return [42]

    monkeypatch.setattr(delivery, "_send_reply_chunk", send)
    with diagnostic_scope(request_id="tg-42"):
        if fails:
            with pytest.raises(RuntimeError) as raised:
                delivery.send_reply(
                    "PRIVATE_TOKEN",
                    "12345",
                    "PRIVATE_STORY",
                    session_id="story-a",
                    app_settings=make_test_settings(home=tmp_path),
                )
            assert raised.value is failure
        else:
            assert (
                delivery.send_reply(
                    "PRIVATE_TOKEN",
                    "12345",
                    "PRIVATE_STORY",
                    session_id="story-a",
                    app_settings=make_test_settings(home=tmp_path),
                )
                is None
            )
    records = events(caplog, "delivery.reply_")
    assert len(records) == 2
    assert records[-1]["status"] == ("failed" if fails else "succeeded")
    assert all(item["request_id"] == "tg-42" for item in records)
    assert records[-1]["session_ref"] == scope_reference("session", "story-a")
    assert "PRIVATE_" not in json.dumps(records)
    assert diagnostic_context() == {}


def test_director_reports_rejected_decision_without_private_explanation(tmp_path, caplog):
    from bridge.director_service import DirectorService
    from bridge.miniapp_context import current_session

    caplog.set_level(logging.INFO, logger="bridge.events")
    services = make_services(tmp_path)
    session = current_session(services, identity(), {})["session"]
    with closing(services.db_factory()) as db:
        result = DirectorService().reassess(
            db,
            "",
            "12345",
            session,
            provider_port=services.provider,
            app_settings=services.config,
            reason="PRIVATE_REASON",
        )
    records = events(caplog, "director.reassessment_")
    assert len(records) == 2
    assert result.result == "rejected"
    assert records[-1]["status"] == "rejected"
    assert records[-1]["revision"] == result.expected_revision
    assert "PRIVATE" not in json.dumps(records)
    assert "message" not in records[-1]


def test_miniapp_request_and_session_share_id_but_not_authorization(tmp_path, caplog):
    from bridge.miniapp_config import load_miniapp_config
    from bridge.miniapp_http import create_miniapp_app

    caplog.set_level(logging.INFO, logger="bridge.events")
    services = make_services(tmp_path)

    async def run():
        async with TestClient(TestServer(create_miniapp_app(services, load_miniapp_config(services.config)))) as client:
            assert (await client.get("/api/v1/session")).status == 401
            headers = {"Authorization": "tma " + signed_data()}
            assert (await client.get("/api/v1/session", headers=headers)).status == 200

    asyncio.run(run())
    records = events(caplog, "miniapp.")
    sessions = [item for item in records if item["event"] == "miniapp.session_finish"]
    assert len(sessions) == 1
    assert len({item["request_id"] for item in records}) == 1
    assert sessions[0]["chat_ref"] == scope_reference("chat", "12345")
    assert records[-1]["http_status"] == 200
    assert services.config.bot_token not in json.dumps(records)


def test_operation_observer_never_retains_payloads_or_interprets_unknown_decisions(caplog):
    import gc
    import weakref

    from bridge.diagnostic_operations import observe_boundary

    class Payload:
        pass

    caplog.set_level(logging.INFO, logger="bridge.events")

    @observe_boundary("director.synthetic", decision=True)
    def operation(chat_id, session, payload):
        return SimpleNamespace(result="PRIVATE_RESULT", expected_revision=3, proposal=payload)

    payload = Payload()
    reference = weakref.ref(payload)
    result = operation("12345", {"session_id": "story-a"}, payload)
    del result, payload
    gc.collect()
    assert reference() is None
    records = events(caplog, "director.synthetic")
    assert records[-1]["status"] == "unknown"
    assert "PRIVATE" not in json.dumps(records)


def test_memory_repair_phase_is_explicit_and_does_not_log_invalid_output(caplog):
    from bridge.memory_response import generate_memory_response

    caplog.set_level(logging.INFO, logger="bridge.events")
    phases = []
    replies = iter(["PRIVATE_INVALID_OUTPUT", "{}"])

    def generate(*_args, **_kwargs):
        phases.append(diagnostic_context().get("phase"))
        return next(replies)

    with diagnostic_scope(chat_id="12345", session_id="story-a", request_id="memory-test"):
        assert (
            generate_memory_response(
                generate, "PRIVATE_KEY", "model", [], parser=json.loads, session_id="private-transport-id", settings={}
            )
            == {}
        )
    assert phases == ["extraction", "json_repair"]
    records = events(caplog, "memory.response_")
    assert records[-1]["accepted"] is True
    assert records[-1]["phase"] == "json_repair"
    assert any(item.get("reason") == "malformed_json" for item in records)
    assert "PRIVATE" not in json.dumps(records)


def test_tracker_publication_is_staged_not_claimed_durable_before_commit(tmp_path, caplog):
    from bridge.memory_draft_publish import publish_simulation
    from bridge.miniapp_context import current_session
    from bridge.sqlite_store import write_transaction

    caplog.set_level(logging.INFO, logger="bridge.events")
    services = make_services(tmp_path)
    session = current_session(services, identity(), {})["session"]
    with closing(services.db_factory()) as db:
        with write_transaction(db):
            source = db.execute(
                "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
                ("12345", session["session_id"], "assistant", "PRIVATE_STORY", 1.0),
            ).lastrowid
            publish_simulation(db, "12345", session["session_id"], {}, source)
    records = events(caplog, "tracker.publication_")
    assert len(records) == 2
    assert records[-1]["status"] == "staged"
    assert records[-1]["session_ref"] == scope_reference("session", session["session_id"])
