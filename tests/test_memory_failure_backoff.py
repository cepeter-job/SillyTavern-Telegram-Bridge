"""Durable-memory failure classes that need non-default retry policy."""

import time

from test_memory_completion_safety import session_db as session_db

from bridge import memory_workers
from bridge.memory_store import claim_jobs
from bridge.model_router import ModelRoutingError


def test_model_configuration_failure_uses_long_backoff(session_db, monkeypatch):
    settings, db, session = session_db
    db.execute("INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('chat','s1','user','event',1)")
    db.commit()
    claim = claim_jobs(db, layers=("summary",))[0]

    def fail_route(*_args, **_kwargs):
        raise ModelRoutingError("retired provider")

    monkeypatch.setattr(memory_workers, "_run_derived_layer", fail_route)
    started = time.time()
    assert (
        memory_workers.run_memory_claim(
            db, claim, session, {"name": "Alice"}, provider_port=None, app_settings=settings
        )
        == "configuration"
    )
    error, next_attempt = db.execute(
        "SELECT last_error,next_attempt_at FROM memory_jobs WHERE layer='summary'"
    ).fetchone()
    assert error == "configuration"
    assert next_attempt >= started + 3599
