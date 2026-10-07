"""Retirement requests and SDK transport closure must share an owned event loop."""

import asyncio
import sqlite3

import pytest
from hindsight_client import Hindsight
from settings_test_support import make_test_settings

from bridge import memory_backend
from bridge.schema import initialize_database_schema


@pytest.mark.parametrize("unavailable", [False, True])
def test_retirement_closes_sdk_on_request_loop(monkeypatch, unavailable):
    db = sqlite3.connect(":memory:")
    initialize_database_schema(db)
    db.execute(
        "INSERT INTO memory_retired_documents(chat_id,session_id,document_id) VALUES('c','old','retired-document')"
    )
    db.commit()
    client = Hindsight(base_url="http://127.0.0.1:8888")
    observed = []
    original_close = client._api_client.close

    async def delete_document(**kwargs):
        observed.append(("request", asyncio.get_running_loop()))
        if unavailable:
            raise RuntimeError("synthetic offline Hindsight")

    async def close_transport():
        observed.append(("close", asyncio.get_running_loop()))
        await original_close()

    monkeypatch.setattr(client.documents, "delete_document", delete_document)
    monkeypatch.setattr(client._api_client, "close", close_transport)
    monkeypatch.setattr(memory_backend, "hindsight_client", lambda **kwargs: client)
    try:
        result = memory_backend.cleanup_retired_memory_documents(db, "c", "old", app_settings=make_test_settings())
        assert result is (not unavailable)
        assert [event for event, _loop in observed] == ["request", "close"]
        request_loop, close_loop = (loop for _event, loop in observed)
        assert request_loop is close_loop
        assert request_loop.is_closed()
        assert db.execute("SELECT deleted FROM memory_retired_documents").fetchone() == (int(not unavailable),)
    finally:
        for loop in {loop for _event, loop in observed}:
            if not loop.is_closed():
                loop.close()
        asyncio.set_event_loop(None)
        db.close()
