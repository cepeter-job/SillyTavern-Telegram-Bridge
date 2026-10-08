"""Behavioral contracts for content-free, bounded troubleshooting events."""

import contextvars
import gc
import importlib.util
import json
import logging
import weakref
from concurrent.futures import ThreadPoolExecutor


def test_diagnostics_module_exists():
    assert importlib.util.find_spec("bridge.diagnostic_events") is not None


def test_scope_is_nested_and_identifiers_are_pseudonymous():
    from bridge.diagnostic_events import diagnostic_context, diagnostic_scope, scope_reference

    assert diagnostic_context() == {}
    with diagnostic_scope(request_id="tg-11", chat_id="12345", session_id="private story"):
        outer = diagnostic_context()
        assert outer["request_id"] == "tg-11"
        assert outer["chat_ref"] == scope_reference("chat", "12345")
        assert "12345" not in json.dumps(outer)
        assert "private story" not in json.dumps(outer)
        with diagnostic_scope(job_id=9):
            assert diagnostic_context()["job_id"] == 9
        assert diagnostic_context() == outer
    assert diagnostic_context() == {}


def test_bound_worker_keeps_only_diagnostic_context_and_restores_thread():
    from bridge.diagnostic_events import bind_diagnostics, diagnostic_context, diagnostic_scope

    class Payload:
        pass

    unrelated = contextvars.ContextVar("private_payload")
    payload = Payload()
    reference = weakref.ref(payload)
    token = unrelated.set(payload)
    with diagnostic_scope(request_id="tg-12"):
        worker = bind_diagnostics(diagnostic_context)
    unrelated.reset(token)
    del payload
    gc.collect()
    assert reference() is None
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(worker).result() == {"request_id": "tg-12"}
        assert pool.submit(diagnostic_context).result() == {}


def test_event_is_allowlisted_and_bounded(caplog):
    from bridge.diagnostic_events import diagnostic_scope, event

    with caplog.at_level(logging.INFO), diagnostic_scope(request_id="tg-13"):
        event("provider.finish", model="a/model", input_tokens=4, prompt="private story", response="private reply")
        event("bad\nevent", model="x" * 10000, input_tokens=-1)
    first, second = [r.diagnostic_fields for r in caplog.records if hasattr(r, "diagnostic_fields")]
    assert first["request_id"] == "tg-13"
    assert first["input_tokens"] == 4
    assert first["model"] == "a/model"
    assert "private" not in json.dumps(first)
    assert second["event"] == "diagnostic.invalid_event"
    assert len(json.dumps(second)) < 2048
    assert "input_tokens" not in second


def test_event_does_not_call_unknown_object_string(caplog):
    from bridge.diagnostic_events import event

    class Untrusted:
        def __str__(self):
            raise AssertionError("must not stringify unknown values")

    with caplog.at_level(logging.INFO):
        event("job.failed", reason=Untrusted(), error=Untrusted())
    assert caplog.records[-1].diagnostic_fields == {"event": "job.failed"}


def test_logging_failure_does_not_break_work(monkeypatch):
    from bridge.diagnostic_events import event

    def unavailable(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(logging.getLogger("bridge.events"), "log", unavailable)
    event("job.started", job_id=1)


def test_error_event_preserves_code_locations_without_exception_content(caplog):

    from bridge.diagnostic_events import event
    from bridge.diagnostic_logging import DiagnosticFormatter

    with caplog.at_level(logging.ERROR, logger="bridge.events"):
        try:
            raise RuntimeError("PRIVATE_EXCEPTION_BODY")
        except RuntimeError:
            event("diagnostic.error", level=logging.ERROR, exc_info=True)
    record = caplog.records[-1]
    assert "PRIVATE_EXCEPTION_BODY" not in caplog.text
    assert record.exc_info is None
    assert record.diagnostic_exception["error_type"] == "RuntimeError"
    result = json.loads(DiagnosticFormatter().format(record))
    assert result["error_type"] == "RuntimeError"
    assert result["traceback"][-1]["function"] == "test_error_event_preserves_code_locations_without_exception_content"
    assert "PRIVATE_EXCEPTION_BODY" not in json.dumps(result)


def test_exception_events_do_not_retain_frame_locals(caplog):
    import gc
    import weakref

    from bridge.diagnostic_events import event

    class PrivatePayload:
        pass

    def emit():
        payload = PrivatePayload()
        reference = weakref.ref(payload)
        try:
            raise RuntimeError("PRIVATE_ERROR")
        except RuntimeError:
            event("diagnostic.failure", level=logging.ERROR, exc_info=True)
        return reference

    with caplog.at_level(logging.ERROR):
        reference = emit()
    gc.collect()
    assert reference() is None
    assert caplog.records[-1].exc_info is None
    assert "PRIVATE_ERROR" not in caplog.text
