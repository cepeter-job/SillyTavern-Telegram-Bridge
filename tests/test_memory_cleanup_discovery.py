"""Legacy enumeration must preserve new generations and bounded progress."""

from memory_cleanup_test_support import (
    append,
    drain,
    fact,
    local_service,
    reset,
    run_fact,
    run_submitted,
)
from memory_cleanup_test_support import (
    cleanup_runtime as cleanup_runtime,
)
from memory_runtime_test_support import isolated_memory_runtime as isolated_memory_runtime

from bridge import memory_backend
from bridge.memory_workers import dispatch_memory_backlog
from bridge.session_core import delete_session_data


def tags(epoch=0, incarnation=1):
    return ["session:s", *memory_backend.hindsight_generation_tags("c", "s", incarnation, epoch)]


def test_delete_recreate_and_two_resets_preserve_new_generation(cleanup_runtime):
    rt = cleanup_runtime
    append(rt)
    old_tags = tags()
    rt.archive.objects.update({"old-v2": old_tags, "legacy-owned": ["session:s"], "other": ["session:other"]})
    assert delete_session_data(rt.db, "c", "s", "other", memory_service=local_service()) == (True, "deleted")
    rt.db.execute(
        "INSERT INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,world_file,created_at,"
        "updated_at) VALUES('c','s','New','','m','','',99,99)"
    )
    rt.db.commit()
    append(rt, "new incarnation")
    assert rt.db.execute("SELECT purge_epoch FROM memory_layer_state WHERE layer='hindsight'").fetchone() == (1,)
    rt.archive.objects["middle-v2"] = tags(1, 99)
    reset(rt, "second")
    reset(rt, "second")
    append(rt, "newest canonical")
    newest = fact(rt, "Mira keeps the new blue key")
    assert run_fact(rt) == "complete"
    rt.archive.objects["new-unmapped-v2"] = tags(2, 99)
    assert rt.db.execute("SELECT count(*) FROM memory_cleanup_discovery").fetchone() == (2,)
    drain(rt)
    assert set(rt.archive.objects) >= {newest, "new-unmapped-v2", "other"}
    assert not {"old-v2", "middle-v2", "legacy-owned"}.intersection(rt.archive.objects)
    assert rt.db.execute("SELECT count(*) FROM memory_cleanup_discovery WHERE phase<>'complete'").fetchone() == (0,)


def test_pagination_keeps_deletion_paused_and_restarts_verification(cleanup_runtime):
    rt = cleanup_runtime
    append(rt)
    rt.archive.objects.update({f"old-{n:04d}": ["session:s"] for n in range(1105)})
    rt.archive.objects["other"] = ["session:other"]
    reset(rt)
    # First pass enumerates 1000 + 105 old objects, at most two calls.
    assert dispatch_memory_backlog(rt, rt.db) == 1
    run_submitted(rt)
    assert len(rt.archive.pages) <= 2
    assert rt.archive.deleted == []
    rt.archive.objects["new-v2"] = tags(1)
    drain(rt)
    assert rt.archive.objects == {"new-v2": tags(1), "other": ["session:other"]}
    assert all(limit == 1000 for _, _, limit, _ in rt.archive.pages)
    assert any(offset == 1000 for _, _, _, offset in rt.archive.pages)
    assert sum(offset == 0 for _, query_tags, _, offset in rt.archive.pages if query_tags) >= 2


def test_missing_provenance_is_deferred_without_deletion(cleanup_runtime):
    rt = cleanup_runtime
    append(rt)
    reset(rt)
    rt.archive.objects["unproven"] = tags(1)
    rt.archive.omit_tags = True
    assert dispatch_memory_backlog(rt, rt.db) == 1
    run_submitted(rt)
    assert rt.archive.objects == {"unproven": tags(1)}
    assert rt.db.execute("SELECT phase,offset,scan_found FROM memory_cleanup_discovery").fetchone() == ("verify", 0, 0)
    assert rt.db.execute("SELECT next_attempt_at FROM memory_cleanup_discovery").fetchone()[0] > 0


def test_off_mode_rejection_and_restart_keep_cleanup_durable(cleanup_runtime):
    rt = cleanup_runtime
    append(rt)
    rt.archive.objects["legacy"] = ["session:s"]
    rt.db.execute("INSERT INTO hindsight_documents VALUES('c','s','legacy','conversation',1)")
    rt.db.execute("INSERT INTO meta(key,value) VALUES('memory_mode:c','off')")
    rt.db.commit()
    reset(rt)
    submit = rt.background.submit
    rt.background.submit = lambda *args: False
    assert dispatch_memory_backlog(rt, rt.db) == 0
    assert not rt.db.execute("SELECT 1 FROM memory_cleanup_discovery WHERE lease_token<>''").fetchone()
    rt.background.submit = submit
    rt.db.execute(
        "UPDATE memory_cleanup_discovery SET lease_token='interrupted',lease_deadline=99999999999,next_attempt_at=0"
    )
    rt.db.execute("UPDATE memory_retired_documents SET next_attempt_at=0")
    rt.db.commit()
    rt.archive.fail_list = True
    assert dispatch_memory_backlog(rt, rt.db, startup=True) == 1
    run_submitted(rt)
    assert dispatch_memory_backlog(rt, rt.db) == 1  # known exact deletion despite list outage
    run_submitted(rt)
    assert "legacy" not in rt.archive.objects
    assert not rt.archive.retained
    rt.archive.fail_list = False
    drain(rt)
    assert rt.db.execute("SELECT phase FROM memory_cleanup_discovery").fetchone() == ("complete",)


def test_deferred_discovery_does_not_starve_new_native_ingestion(cleanup_runtime):
    rt = cleanup_runtime
    append(rt)
    reset(rt)
    rt.archive.fail_list = True
    assert dispatch_memory_backlog(rt, rt.db) == 1
    run_submitted(rt)
    # Drain the finite known IDs while the discovery retry is deferred.
    while dispatch_memory_backlog(rt, rt.db):
        run_submitted(rt)
    append(rt, "fresh source")
    doc = fact(rt, "Mira has the new key")
    assert run_fact(rt) == "complete"
    assert doc in rt.archive.objects
