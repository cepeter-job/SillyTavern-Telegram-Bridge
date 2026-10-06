"""Same-session progress through the actual scheduled Hindsight worker."""

import time
from types import SimpleNamespace

import pytest
from application_test_setup import make_test_provider_port
from memory_runtime_test_support import isolated_memory_runtime as isolated_memory_runtime
from settings_test_support import make_test_settings
from test_memory_final_integration import FIELDS, SESSION, Archive, append

from bridge import memory_backend, memory_workers
from bridge.memory_fact_store import load_source, remember_local_fact
from bridge.memory_store import claim_jobs, next_source_segment
from bridge.sqlite_store import db_connect


@pytest.fixture
def work(tmp_path, monkeypatch, isolated_memory_runtime):
    settings = make_test_settings(home=tmp_path, db_file=tmp_path / "fairness.sqlite")
    clock = [time.time()]
    monkeypatch.setattr(memory_backend.time, "time", lambda: clock[0])
    archive = Archive()
    attempted, failures, submitted = [], set(), []
    delete = archive.delete_document

    async def slow_delete(**kwargs):
        document_id = kwargs["document_id"]
        attempted.append(document_id)
        if document_id in failures:
            raise RuntimeError("Synthetic finite retirement outage")
        clock[0] += 10
        await delete(**kwargs)

    def unexpected_provider(*args, **kwargs):
        pytest.fail("Raw/index fairness must not require a model request")

    monkeypatch.setattr(archive, "delete_document", slow_delete)
    monkeypatch.setattr(memory_backend, "hindsight_client", lambda **kwargs: archive)
    monkeypatch.setattr(memory_workers, "card_fields_from_file", lambda *args, **kwargs: dict(FIELDS))
    session = dict(SESSION, character_file="synthetic.png")
    services = SimpleNamespace(
        config=settings,
        db_factory=lambda: db_connect(app_settings=settings),
        session=SimpleNamespace(load=lambda *args: session),
        provider=make_test_provider_port(generate_backend=unexpected_provider),
        background=SimpleNamespace(submit=lambda *args: submitted.append(args) or True),
    )
    # All consumed external/default/card seams are explicit before construction.
    db = db_connect(app_settings=settings)
    db.execute(
        "INSERT INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,"
        "world_file,created_at,updated_at) VALUES('c','s','Story','synthetic.png','m','','',1,1)"
    )
    db.commit()
    row = append(db, "Fresh canonical source must reach the archive")
    remember_local_fact(db, "c", "s", "Bob", "The brass key opens the north gate.")
    source = next_source_segment(db, "c", "s", "hindsight")
    native_id, state = db.execute("SELECT document_id,state FROM memory_fact_index").fetchone()
    assert state == "pending"
    watched = {f"watched-raw-{number:02}" for number in range(32)}
    for document_id in sorted(watched):
        db.execute(
            "INSERT INTO memory_archival_attempts(attempt_token,document_id,chat_id,session_id) VALUES(?,?,?,?)",
            (f"unknown-{document_id}", document_id, "c", "s"),
        )
        db.execute(
            "INSERT INTO memory_retired_documents(chat_id,session_id,document_id) VALUES('c','s',?)",
            (document_id,),
        )
        archive.remote[document_id] = {}
    db.execute("UPDATE memory_jobs SET next_attempt_at=9999999999 WHERE layer<>'hindsight'")
    db.commit()
    yield SimpleNamespace(
        db=db,
        settings=settings,
        clock=clock,
        archive=archive,
        attempted=attempted,
        failures=failures,
        submitted=submitted,
        services=services,
        row=row,
        source=source,
        native_id=native_id,
        watched=watched,
    )
    db.close()


def dispatch_and_execute(work):
    assert memory_workers.dispatch_memory_backlog(work.services, work.db) == 1
    assert len(work.submitted) == 1
    _, callback, *args = work.submitted.pop()
    callback(*args)
    return callback.__name__


def assert_published(work):
    db, source = work.db, work.source
    dirty, completed, error = db.execute(
        "SELECT dirty_version,completed_version,last_error FROM memory_jobs WHERE layer='hindsight'"
    ).fetchone()
    progress = {
        "raw_remote": source.document_id in work.archive.remote,
        "raw_accepted": load_source(db, source.document_id) is not None,
        "raw_mapping": db.execute(
            "SELECT 1 FROM hindsight_documents WHERE document_id=? AND kind='source_segment'", (source.document_id,)
        ).fetchone()
        is not None,
        "raw_coverage": db.execute("SELECT covered_id FROM memory_layer_state WHERE layer='hindsight'").fetchone()
        == (work.row,),
        "native_remote": work.native_id in work.archive.remote,
        "native_index": db.execute(
            "SELECT state FROM memory_fact_index WHERE document_id=?", (work.native_id,)
        ).fetchone()
        == ("retained",),
        "job_acknowledged": dirty == completed,
    }
    assert all(progress.values()), (progress, error)


def test_same_session_watches_allow_executed_worker_progress(work):
    assert dispatch_and_execute(work) == "_retired_memory_worker"
    assert len(work.attempted) == 16
    assert dispatch_and_execute(work) == "_memory_worker"
    assert_published(work)
    assert len(work.attempted) == 16  # The ordinary turn does not repeat watched cleanup.
    assert dispatch_and_execute(work) == "_retired_memory_worker"
    assert len(work.attempted) == 32
    assert not work.watched.intersection(work.archive.remote)
    before = len(work.attempted)
    assert dispatch_and_execute(work) == "_retired_memory_worker"
    assert 0 < len(work.attempted) - before <= 16
    assert work.db.execute("SELECT count(*) FROM memory_archival_attempts WHERE finished=0").fetchone() == (32,)
    assert work.db.execute(
        "SELECT count(*) FROM memory_retired_documents WHERE deleted=0 AND next_attempt_at>0"
    ).fetchone() == (32,)
    assert_published(work)


def test_finite_prerequisite_failure_blocks_then_retry_publishes(work):
    finite = "zz-finite-retirement"
    work.db.execute("INSERT INTO memory_retired_documents(chat_id,session_id,document_id) VALUES('c','s',?)", (finite,))
    work.db.commit()
    work.archive.remote[finite] = {}
    work.failures.update(work.watched | {finite})
    # A real claimed worker exercises its prerequisite independently of the
    # dispatcher's separate retirement priority.
    claim = claim_jobs(work.db, layers=("hindsight",))[0]
    memory_workers._memory_worker(work.services, claim)
    assert work.attempted == [finite]
    assert work.source.document_id not in work.archive.remote
    assert work.native_id not in work.archive.remote
    assert load_source(work.db, work.source.document_id) is None
    assert work.db.execute("SELECT last_error FROM memory_jobs WHERE layer='hindsight'").fetchone() == ("work_failed",)
    retired_due = work.db.execute(
        "SELECT deleted,next_attempt_at FROM memory_retired_documents WHERE document_id=?", (finite,)
    ).fetchone()
    assert retired_due[0] == 0 and retired_due[1] > work.clock[0]
    job_due = work.db.execute("SELECT next_attempt_at FROM memory_jobs WHERE layer='hindsight'").fetchone()[0]
    work.clock[0] = max(job_due, retired_due[1]) + 1
    work.failures.remove(finite)
    claim = claim_jobs(work.db, layers=("hindsight",))[0]
    memory_workers._memory_worker(work.services, claim)
    assert work.attempted == [finite, finite]
    assert finite not in work.archive.remote
    assert work.watched.issubset(work.archive.remote)
    assert work.db.execute("SELECT count(*) FROM memory_archival_attempts WHERE finished=0").fetchone() == (32,)
    assert_published(work)
