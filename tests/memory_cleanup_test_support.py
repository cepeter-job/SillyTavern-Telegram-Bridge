"""Synthetic provider and isolated lifecycle fixtures for durable cleanup races."""

import threading
from types import SimpleNamespace

import pytest
from settings_test_support import make_test_settings

from bridge import memory_backend, message_commands
from bridge.memory_fact_store import remember_local_fact
from bridge.memory_store import claim_jobs, next_source_segment
from bridge.memory_workers import dispatch_memory_backlog, run_memory_claim
from bridge.sqlite_store import db_connect


class Archive:
    def __init__(self):
        self.documents = self
        self.objects = {}
        self.deleted = []
        self.retained = []
        self.pages = []
        self.before_retain = None
        self.after_delete = None
        self.fail_retain = False
        self.fail_delete = False
        self.fail_list = False
        self.omit_tags = False

    def unlocked(self):
        assert not memory_backend.hindsight_session_lock("c", "s")._is_owned()

    def retain(self, **values):
        self.unlocked()
        self.retained.append(values)
        if self.before_retain:
            self.before_retain(values)
        if self.fail_retain:
            raise RuntimeError("synthetic uncertain retain")
        self.objects[values["document_id"]] = list(values["tags"])

    async def delete_document(self, *, bank_id, document_id):
        self.unlocked()
        if self.fail_delete:
            raise RuntimeError("synthetic delete outage")
        self.deleted.append(document_id)
        self.objects.pop(document_id, None)
        if self.after_delete:
            self.after_delete(document_id)

    async def list_documents(self, *, bank_id, limit, offset, q=None, tags=None, tags_match=None):
        self.unlocked()
        self.pages.append((q, tags, limit, offset))
        if self.fail_list:
            raise RuntimeError("synthetic discovery outage")
        items = [
            {"id": key, **({} if self.omit_tags else {"tags": value})}
            for key, value in sorted(self.objects.items())
            if (not q or q in key) and (not tags or set(tags).intersection(value))
        ]
        return SimpleNamespace(items=items[offset : offset + limit], total=len(items))

    def close(self):
        self.unlocked()


@pytest.fixture
def cleanup_runtime(tmp_path, monkeypatch, isolated_memory_runtime):
    settings = make_test_settings(home=tmp_path, db_file=tmp_path / "cleanup.sqlite")
    db = db_connect(app_settings=settings)
    db.execute(
        "INSERT INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,world_file,created_at,"
        "updated_at) VALUES('c','s','Story','','m','','',1,1)"
    )
    db.commit()
    archive = Archive()

    def client(**kwargs):
        archive.unlocked()
        return archive

    monkeypatch.setattr(memory_backend, "hindsight_client", client)
    monkeypatch.setattr(message_commands, "telegram_request", lambda *args, **kwargs: None)
    runtime = SimpleNamespace(
        db=db,
        config=settings,
        archive=archive,
        db_factory=lambda: db_connect(app_settings=settings),
        session={"session_id": "s", "chat_id": "c", "title": "Story", "character_file": "", "model_id": "m"},
        submitted=[],
    )
    runtime.background = SimpleNamespace(submit=lambda *args: runtime.submitted.append(args) or True)
    yield runtime
    db.close()


def append(runtime, text="old canonical source"):
    runtime.db.execute(
        "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('c','s','user',?,2)", (text,)
    )
    runtime.db.commit()
    return next_source_segment(runtime.db, "c", "s", "hindsight")


def fact(runtime, text="Mira has a red key"):
    remember_local_fact(runtime.db, "c", "s", "Mira", text)
    return runtime.db.execute("SELECT document_id FROM memory_fact_index ORDER BY created_at DESC LIMIT 1").fetchone()[
        0
    ]


def local_service():
    def queue(db, chat_id, session_id):
        from bridge.memory_retirement_store import queue_session_memory_cleanup

        queue_session_memory_cleanup(db, chat_id, session_id)

    def forbidden(*args):
        pytest.fail("Lifecycle called synchronous remote purge")

    return SimpleNamespace(queue_cleanup=queue, purge_session=forbidden)


def reset(runtime, operation_id=None, npc=None):
    message_commands.reset_session(
        runtime.db,
        "token",
        "c",
        runtime.session,
        operation_id=operation_id,
        memory_service=local_service(),
        npc_service=npc or SimpleNamespace(purge_session=lambda *args: None),
    )


def run_fact(runtime):
    claim = claim_jobs(runtime.db, layers=("hindsight",))[0]
    return run_memory_claim(runtime.db, claim, runtime.session, {"name": "Mira"}, app_settings=runtime.config)


def run_submitted(runtime):
    _, function, *args = runtime.submitted.pop(0)
    function(*args)


def drain(runtime, limit=200):
    for _ in range(limit):
        runtime.db.execute("UPDATE memory_jobs SET completed_version=dirty_version")
        runtime.db.execute("UPDATE memory_retired_documents SET next_attempt_at=0")
        runtime.db.execute("UPDATE memory_cleanup_discovery SET next_attempt_at=0")
        runtime.db.commit()
        if not dispatch_memory_backlog(runtime, runtime.db):
            break
        run_submitted(runtime)


def start_call(runtime, callback):
    done = threading.Event()
    values, errors = [], []

    def run():
        db = runtime.db_factory()
        try:
            values.append(callback(db))
        except BaseException as exc:
            errors.append(exc)
        finally:
            db.close()
            done.set()

    thread = threading.Thread(target=run)
    thread.start()
    return thread, done, values, errors
