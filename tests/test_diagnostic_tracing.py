"""Correlate provider attempts and recovered workers without payload logging."""

import json
import logging
import sqlite3
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from bridge.diagnostic_events import diagnostic_context, diagnostic_scope, scope_reference
from bridge.provider_errors import ProviderRequestError
from bridge.provider_port import ProviderPort
from bridge.token_usage_values import TokenUsage


class Policy:
    def candidates(self, model, purpose):
        return (model, "secondary::fallback")

    def begin(self, model):
        return SimpleNamespace(selection=model)

    def succeed(self, attempt):
        pass

    def fail(self, attempt, error):
        pass

    def cancel(self, attempt):
        pass


def events(caplog, name):
    return [
        record.diagnostic_fields
        for record in caplog.records
        if hasattr(record, "diagnostic_fields") and record.diagnostic_fields["event"] == name
    ]


def test_provider_fallback_is_correlated_and_accounts_for_both_attempts(caplog):
    calls = []
    recorded = []

    def backend(api_key, model, messages, **kwargs):
        calls.append(model)
        kwargs["usage_callback"](TokenUsage(10, 2, 12))
        if len(calls) == 1:
            raise ProviderRequestError(model, "timeout")
        return "private reply"

    port = ProviderPort(backend, usage_recorder=recorded.append, policy=Policy()).for_usage("111", "story-a", "story")
    with caplog.at_level(logging.INFO), diagnostic_scope(request_id="tg-1", job_id=8):
        assert (
            port.generate("synthetic-private-key", "primary::model", [{"content": "private story"}]) == "private reply"
        )
    started = events(caplog, "provider.start")
    finished = events(caplog, "provider.finish")
    assert len(started) == len(finished) == 2
    assert {record["request_id"] for record in started + finished} == {"tg-1"}
    assert len({record["call_id"] for record in started + finished}) == 1
    assert [record["attempt"] for record in started] == [1, 2]
    assert [record["status"] for record in finished] == ["failed", "succeeded"]
    assert sum(record["input_tokens"] for record in finished) == 20
    assert finished[0]["usage_complete"] is False
    assert finished[1]["usage_complete"] is True
    fallback = events(caplog, "provider.fallback")[0]
    assert fallback["reason"] == "timeout"
    assert fallback["next_model"] == "secondary::fallback"
    assert len(recorded) == 2
    assert "private story" not in json.dumps(started + finished)
    assert "synthetic-private-key" not in json.dumps(started + finished)


def test_visible_stream_suppresses_fallback_and_explains_why(caplog):
    def backend(api_key, model, messages, **kwargs):
        kwargs["stream_callback"]("visible reply")
        raise ProviderRequestError(model, "timeout")

    port = ProviderPort(backend, policy=Policy()).for_usage("111", "a", "story")
    with caplog.at_level(logging.INFO), pytest.raises(ProviderRequestError):
        port.generate("", "primary::model", [], stream_callback=lambda text: None)
    assert len(events(caplog, "provider.start")) == 1
    fallback = events(caplog, "provider.fallback")[0]
    assert fallback["status"] == "suppressed"
    assert fallback["phase"] == "visible_output"


@pytest.mark.parametrize(
    "purpose", ["story", "director", "summary", "npc", "scene", "choices", "memory", "adjudication", "image_prompt"]
)
def test_all_model_roles_are_named_without_fabricating_usage(caplog, purpose):
    port = ProviderPort(lambda *args, **kwargs: "reply").for_usage("1", "2", purpose)
    with caplog.at_level(logging.INFO):
        port.generate("", "primary::model", [])
    item = events(caplog, "provider.finish")[0]
    assert item["purpose"] == purpose
    assert item["usage_reported"] is False
    assert "input_tokens" not in item


def test_durable_worker_reconstructs_identity_instead_of_using_dispatcher_context():
    from bridge.scheduler_safety import DurableWorkerGuard

    with closing(sqlite3.connect(":memory:")) as db, db:
        db.execute(
            "CREATE TABLE jobs(job_id INTEGER,update_id INTEGER,chat_id TEXT,"
            "session_id TEXT,kind TEXT,attempts INTEGER)"
        )
        db.execute("INSERT INTO jobs VALUES(8,12,'111','story-a','generation',1)")
        guard = DurableWorkerGuard(lambda *args, **kwargs: None)
        worker = guard.prepare(db, 8, diagnostic_context)
        with diagnostic_scope(request_id="tg-other", session_id="other"):
            actual = worker()
            assert diagnostic_context()["request_id"] == "tg-other"
    assert actual["request_id"] == "tg-12"
    assert actual["job_id"] == 8
    assert actual["session_ref"] == scope_reference("session", "story-a")
    assert actual["attempt"] == 2


def test_background_future_preserves_scope_and_releases_it(monkeypatch):
    import bridge.background as background

    with ThreadPoolExecutor(max_workers=1) as executor:
        monkeypatch.setattr(background, "_UTILITY_EXECUTOR", executor)
        monkeypatch.setattr(background, "_BACKGROUND_ACCEPTING", True)
        with diagnostic_scope(request_id="tg-9", job_id=7):
            future = background._submit_tracked_future("test", diagnostic_context)
        assert future.result(timeout=5)["request_id"] == "tg-9"
        assert executor.submit(diagnostic_context).result(timeout=5) == {}


def test_provider_cannot_inherit_another_sessions_job_or_request(caplog):
    seen = []

    def backend(*_args, **_kwargs):
        seen.append(diagnostic_context())
        return "reply"

    with (
        caplog.at_level(logging.INFO),
        diagnostic_scope(
            request_id="other-request", job_id=77, worker_id="other-worker", chat_id="22", session_id="other"
        ),
    ):
        ProviderPort(backend).for_usage("11", "saved", "summary").generate("", "model", [])
    assert seen[0]["request_id"] != "other-request"
    assert "job_id" not in seen[0]
    assert "worker_id" not in seen[0]
    assert seen[0]["chat_ref"] == scope_reference("chat", "11")
    assert seen[0]["session_ref"] == scope_reference("session", "saved")
    assert diagnostic_context() == {}


def test_failed_provider_attempt_survives_warning_only_logging(caplog):
    import pytest

    from bridge.provider_errors import ProviderRequestError

    def backend(*_args, **_kwargs):
        raise TimeoutError("PRIVATE_TIMEOUT")

    with caplog.at_level(logging.WARNING), pytest.raises(ProviderRequestError):
        ProviderPort(backend).for_usage("11", "saved", "summary").generate("", "model", [])
    failed = events(caplog, "provider.finish")
    assert len(failed) == 1
    assert failed[0]["status"] == "failed"
    assert failed[0]["reason"] == "timeout"
