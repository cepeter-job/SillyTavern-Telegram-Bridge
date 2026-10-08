import json
import logging
from dataclasses import dataclass, replace

import pytest

from bridge.diagnostic_events import diagnostic_context, diagnostic_scope, scope_reference


@dataclass(frozen=True)
class Claim:
    chat_id: str = "12345"
    session_id: str = "private-story"
    session_created_at: float = 1.0
    layer: str = "summary"
    version: int = 2
    target_id: int = 9
    token: str = "PRIVATE_LEASE"
    rewrite_identity: int = 3
    purge_epoch: int = 0


def test_claim_identity_is_stable_without_leases_and_changes_for_rewrites():
    from bridge.memory_work_diagnostics import memory_identity

    claim = Claim()
    first = memory_identity(claim)
    assert first == memory_identity(replace(claim, token="NEW_PRIVATE_LEASE"))
    assert first["request_id"] != memory_identity(replace(claim, rewrite_identity=4))["request_id"]
    assert first["request_id"] != memory_identity(replace(claim, session_created_at=2.0))["request_id"]
    assert first["session_ref"] == scope_reference("session", claim.session_id)
    assert first["revision"] == 3
    assert first["source_message_id"] == 9
    assert "PRIVATE" not in json.dumps(first)
    assert "private-story" not in json.dumps(first)


@pytest.mark.parametrize(
    "result,status",
    [("complete", "succeeded"), ("deferred", "deferred"), ("stale_source", "rejected"), ("malformed_json", "failed")],
)
def test_claim_execution_is_isolated_and_preserves_domain_result(caplog, result, status):
    from bridge.memory_work_diagnostics import observe_memory_claim

    caplog.set_level(logging.INFO, logger="bridge.events")
    seen = []

    @observe_memory_claim
    def run(_db, _claim, **_kwargs):
        seen.append(diagnostic_context())
        return result

    with diagnostic_scope(request_id="wrong-dispatcher", job_id=99, session_id="wrong-story"):
        assert run(None, Claim(), source_text="PRIVATE_STORY") == result
        assert diagnostic_context()["request_id"] == "wrong-dispatcher"
    assert seen[0]["request_id"].startswith("memory-")
    assert "job_id" not in seen[0]
    records = [record.diagnostic_fields for record in caplog.records if hasattr(record, "diagnostic_fields")]
    assert records[-1]["status"] == status
    assert records[-1]["reason"] == result
    assert "PRIVATE" not in json.dumps(records)


def test_claim_exception_is_rethrown_without_logging_its_message(caplog):
    from bridge.memory_work_diagnostics import observe_memory_claim

    caplog.set_level(logging.INFO, logger="bridge.events")

    @observe_memory_claim
    def run(_db, _claim):
        raise RuntimeError("PRIVATE_FAILURE")

    with pytest.raises(RuntimeError):
        run(None, Claim())
    records = [record.diagnostic_fields for record in caplog.records if hasattr(record, "diagnostic_fields")]
    assert records[-1]["status"] == "failed"
    assert records[-1]["error_type"] == "RuntimeError"
    assert "PRIVATE" not in json.dumps(records)
    assert diagnostic_context() == {}
