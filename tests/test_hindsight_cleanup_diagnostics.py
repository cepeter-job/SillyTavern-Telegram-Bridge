"""Cleanup diagnostics distinguish remote failures from durable late-write watches."""

import logging

import pytest
from memory_cleanup_test_support import cleanup_runtime as cleanup_runtime
from memory_cleanup_test_support import fact
from memory_runtime_test_support import isolated_memory_runtime as isolated_memory_runtime

from bridge import memory_backend


@pytest.mark.parametrize(
    ("case", "status", "reason", "deleted", "result"),
    [
        ("terminal", "succeeded", "deleted", 1, True),
        ("uncertain", "deferred", "late_write_watch", 0, True),
        ("failure", "failed", "delete_failed", 0, False),
        ("current", "skipped", "current_source", 0, False),
    ],
)
def test_cleanup_reports_actual_outcome_without_private_values(
    cleanup_runtime, caplog, case, status, reason, deleted, result
):
    rt = cleanup_runtime
    document = fact(rt, "PRIVATE_FACT_TEXT") if case == "current" else "PRIVATE_DOCUMENT_ID"
    rt.db.execute("INSERT INTO memory_retired_documents(chat_id,session_id,document_id) VALUES('c','s',?)", (document,))
    if case == "uncertain":
        rt.db.execute(
            "INSERT INTO memory_archival_attempts(attempt_token,document_id,chat_id,session_id) VALUES(?,?,?,?)",
            ("PRIVATE_ATTEMPT_TOKEN", document, "c", "s"),
        )
    rt.db.commit()
    rt.archive.objects[document] = ["session:s"]
    rt.archive.fail_delete = case == "failure"
    caplog.set_level(logging.INFO, logger="bridge.events")

    assert memory_backend.cleanup_retired_memory_documents(rt.db, "c", "s", app_settings=rt.config) is result

    events = [
        record.diagnostic_fields
        for record in caplog.records
        if getattr(record, "diagnostic_fields", {}).get("event") == "memory.hindsight_cleanup_finish"
    ]
    assert len(events) == 1
    assert events[0]["status"] == status
    assert events[0]["reason"] == reason
    assert events[0]["document_ref"] and events[0]["document_ref"] != document
    assert events[0]["elapsed_ms"] >= 0
    assert "PRIVATE" not in repr(events)
    assert "PRIVATE" not in caplog.text
    assert rt.db.execute(
        "SELECT deleted,attempts FROM memory_retired_documents WHERE document_id=?", (document,)
    ).fetchone() == (deleted, 1)
    if case == "uncertain":
        assert document not in rt.archive.objects
        assert rt.db.execute("SELECT count(*) FROM memory_archival_attempts WHERE finished=0").fetchone() == (1,)
    elif case == "current":
        assert not rt.archive.deleted and document in rt.archive.objects
