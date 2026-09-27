import pytest
from miniapp_test_support import identity, make_services


def test_jobs_are_idempotent_owned_and_durable(tmp_path, monkeypatch):
    import bridge.miniapp_jobs as jobs

    s = make_services(tmp_path)
    w = identity()
    queued = []
    monkeypatch.setattr(jobs, "submit_background", lambda label, work: queued.append(work) or True)
    data = {"operation_id": "once-12345", "session_id": "default"}
    first = jobs.submit_job(s, w, "test", data, lambda *args: {"value": 42})
    second = jobs.submit_job(s, w, "test", data, lambda *args: {"value": 13})
    assert first["id"] == second["id"] and len(queued) == 1
    assert first["state"] == "queued"
    with pytest.raises(ValueError):
        jobs.job_status(s, identity("67890"), {"job_id": first["id"]})
    queued.pop()()
    result = jobs.job_status(s, w, {"job_id": first["id"]})
    assert result["state"] == "succeeded" and result["result"] == {"value": 42}
    with pytest.raises(ValueError):
        jobs.submit_job(s, w, "other", data, lambda *args: {})


def test_restart_marks_unfinished_jobs_interrupted(tmp_path, monkeypatch):
    import bridge.miniapp_jobs as jobs

    s = make_services(tmp_path)
    w = identity()
    monkeypatch.setattr(jobs, "submit_background", lambda *args: True)
    result = jobs.submit_job(s, w, "test", {"operation_id": "never-finished"}, lambda *args: {})
    jobs.recover_interrupted_jobs(s)
    assert jobs.job_status(s, w, {"job_id": result["id"]})["state"] == "interrupted"


def test_job_failures_are_redacted_and_admission_is_bounded(tmp_path, monkeypatch):
    import bridge.miniapp_jobs as jobs

    s = make_services(tmp_path)
    w = identity()
    queued = []
    monkeypatch.setattr(jobs, "submit_background", lambda label, work: queued.append(work) or True)

    def failed(*args):
        raise RuntimeError("secret-provider-token")

    first = jobs.submit_job(s, w, "test", {"operation_id": "failure-1"}, failed)
    with pytest.raises(ValueError):
        jobs.submit_job(s, w, "test", {"operation_id": "failure-2"}, failed)
    queued.pop()()
    state = jobs.job_status(s, w, {"job_id": first["id"]})
    assert state["state"] == "failed"
    assert "secret-provider-token" not in str(state)
