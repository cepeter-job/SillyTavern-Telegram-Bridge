"""Transport completion metadata is factual, allowlisted and independent of usage."""

import logging

import pytest
from test_memory_json_transport import routed_provider


@pytest.mark.parametrize(
    "reason,want,cap",
    [("length", "length", True), ("stop", "stop", False), ("PRIVATE_CANARY", "unknown", None), (None, None, None)],
)
def test_actual_finish_metadata_only(tmp_path, monkeypatch, caplog, reason, want, cap):
    choice = {"message": {"content": "{}"}}
    if reason is not None:
        choice["finish_reason"] = reason
    port, sent = routed_provider(tmp_path, monkeypatch, [{"choices": [choice], "usage": {"completion_tokens": 1000}}])
    caplog.set_level(logging.INFO, logger="bridge.events")
    assert (
        port.generate(
            "",
            "openrouter::test/model",
            [{"role": "user", "content": "PRIVATE_SOURCE"}],
            session_id="s",
            settings={"max_tokens": 1000, "json_once": True},
            force_non_stream=True,
        )
        == "{}"
    )
    records = [
        r.diagnostic_fields
        for r in caplog.records
        if getattr(r, "diagnostic_fields", {}).get("event") == "provider.output_finished"
    ]
    assert records, "transport must report available completion metadata"
    fields = records[0]
    assert fields.get("finish_reason") == want
    assert fields.get("output_cap_reached") is cap
    assert len(sent) == 1
    assert "PRIVATE" not in repr(fields)


@pytest.mark.parametrize(
    "reason,want,cap",
    [("length", "length", True), ("stop", "stop", False), ({"PRIVATE_CANARY": 1}, "unknown", None), (None, None, None)],
)
def test_stream_finish_metadata_remains_private(reason, want, cap, caplog):
    import io
    import json

    from bridge.provider_streaming import read_openai_stream_segment

    response = io.BytesIO(
        (
            "data: "
            + json.dumps({"choices": [{"delta": {"content": "{}"}, "finish_reason": reason}]})
            + "\n\ndata: [DONE]\n"
        ).encode()
    )
    caplog.set_level(logging.INFO, logger="bridge.events")
    content, _, cancelled = read_openai_stream_segment(response, prefix="", stream_callback=None, cancel_event=None)
    assert content == "{}" and not cancelled
    fields = next(
        r.diagnostic_fields
        for r in caplog.records
        if getattr(r, "diagnostic_fields", {}).get("event") == "provider.output_finished"
    )
    assert fields.get("finish_reason") == want
    assert fields.get("output_cap_reached") is cap
    assert "PRIVATE" not in repr(fields)


@pytest.mark.parametrize(
    "reason,want,cap",
    [
        ("max_output_tokens", "max_output_tokens", True),
        ("content_filter", "content_filter", False),
        ("PRIVATE_CANARY", "unknown", None),
        (None, None, None),
    ],
)
def test_responses_api_incomplete_details_are_actual_metadata(reason, want, cap, caplog):
    import json

    from bridge.provider_transport import _opencode_responses_text

    payload = {"output_text": "{}", "status": "incomplete", "usage": {"output_tokens": 1000}}
    if reason is not None:
        payload["incomplete_details"] = {"reason": reason}
    caplog.set_level(logging.INFO, logger="bridge.events")
    assert _opencode_responses_text(json.dumps(payload)) == "{}"
    fields = [
        r.diagnostic_fields
        for r in caplog.records
        if getattr(r, "diagnostic_fields", {}).get("event") == "provider.output_finished"
    ]
    if reason is None:
        assert fields == []
    else:
        assert fields[0].get("finish_reason") == want
        assert fields[0].get("output_cap_reached") is cap
    assert "PRIVATE" not in repr(fields)
