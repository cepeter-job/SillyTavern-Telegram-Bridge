"""Arc outcomes require committed story evidence and follow the same rewind boundary."""

import json
from dataclasses import asdict

import pytest
from test_memory_completion_safety import session_db as session_db
from test_narrative_reconciliation import add_story, proposal, run_reconciliation

from bridge.narrative_arc_repository import load_arc_row, store_arc_row
from bridge.narrative_arcs import load_arcs, parse_arc_updates
from bridge.narrative_repository import load_narrative_clock
from bridge.sqlite_store import write_transaction


def arc(*, status="active", title="Rebellion", quote=None, rowid=None, related=("rebellion",)):
    return {
        "arc_id": "rebellion_arc",
        "title": title,
        "status": status,
        "phase": "resolution" if status == "resolved" else "development",
        "importance": "major",
        "summary": "The rebellion seeks control of the gate.",
        "open_questions": [] if status == "resolved" else ["Who holds the gate?"],
        "related_threads": list(related),
        "evidence": [] if quote is None else [{"rowid": rowid, "quote": quote}],
    }


def packet(*arcs, phase="development"):
    data = json.loads(proposal(phase=phase))
    data["threads"].append(
        {"thread_id": "palace", "title": "Palace", "status": "offscreen", "summary": "The council watches."}
    )
    data["arcs"] = list(arcs)
    return json.dumps(data)


def test_arc_updates_share_one_reconciliation_and_are_session_scoped(session_db):
    _, db, _ = session_db
    rowid = add_story(db, "Mara organizes a rebellion at the gate while the palace watches.")
    calls = []

    def generate(*args, **kwargs):
        assert not db.in_transaction
        calls.append(args)
        return packet(arc(related=("rebellion", "palace")))

    run_reconciliation(session_db, generate)
    assert len(calls) == 1
    current = load_arcs(db, "chat", "s1")
    assert len(current) == 1
    assert current[0].arc_id == "rebellion_arc" and current[0].status == "active"
    assert current[0].source_revision == rowid
    assert current[0].related_threads == ("rebellion", "palace")
    assert load_arcs(db, "other", "s1") == [] and load_arcs(db, "chat", "missing") == []


def test_resolved_arc_copies_only_committed_evidence(session_db):
    _, db, _ = session_db
    add_story(db)
    run_reconciliation(session_db, lambda *a, **k: packet(arc()))
    quote = "Mara took control of the gate. The rebellion was over."
    rowid = add_story(db, quote)
    run_reconciliation(
        session_db, lambda *a, **k: packet(arc(status="resolved", quote=quote, rowid=rowid), phase="resolution")
    )
    current = load_arcs(db, "chat", "s1")[0]
    assert current.status == "resolved" and current.source_revision == rowid
    assert current.evidence[0].quote == quote and current.evidence[0].rowid == rowid
    assert db.execute("SELECT COUNT(*) FROM ending_state").fetchone()[0] == 0


@pytest.mark.parametrize("bad", ["no_evidence", "invented_quote", "future_row", "old_row", "foreign_thread"])
def test_arc_resolution_cannot_be_fabricated_or_reuse_wrong_boundary(session_db, bad):
    _, db, _ = session_db
    old = add_story(db, "A rumor says the rebellion is over.")
    run_reconciliation(session_db, lambda *a, **k: packet(arc()))
    rowid = add_story(db, "Mara waits at the gate.")
    candidate = arc(status="resolved", quote="Mara waits at the gate.", rowid=rowid)
    if bad == "no_evidence":
        candidate["evidence"] = []
    elif bad == "invented_quote":
        candidate["evidence"][0]["quote"] = "PLAN ONLY: the rebellion is already over."
    elif bad == "future_row":
        candidate["evidence"][0]["rowid"] = rowid + 500
    elif bad == "old_row":
        candidate["evidence"] = [{"rowid": old, "quote": "A rumor says the rebellion is over."}]
    else:
        candidate["related_threads"] = ["unknown_thread"]
    before = db.execute("SELECT * FROM messages").fetchall()
    prior = load_arc_row(db, "chat", "s1", "rebellion_arc")
    run_reconciliation(session_db, lambda *a, **k: packet(candidate, phase="resolution"))
    assert load_arc_row(db, "chat", "s1", "rebellion_arc") == prior
    assert load_narrative_clock(db, "chat", "s1")["updated_through_rowid"] == old
    assert db.execute("SELECT * FROM messages").fetchall() == before


def test_hidden_director_intention_does_not_enter_arc_evidence_input(session_db):
    _, db, _ = session_db
    add_story(db)
    with write_transaction(db):
        db.execute(
            "INSERT INTO director_state(chat_id,session_id,active_direction) "
            "VALUES('chat','s1','SECRET_PLANNED_RESOLUTION')"
        )
    seen = []

    def generate(*args, **kwargs):
        seen.append(json.dumps(args[2]))
        return packet(arc())

    run_reconciliation(session_db, generate)
    assert all("SECRET_PLANNED_RESOLUTION" not in prompt for prompt in seen)
    assert load_arcs(db, "chat", "s1")[0].status == "active"


def test_edit_rewinds_arc_outcome_and_excludes_future_summary_from_prompt(session_db):
    _, db, _ = session_db
    old = add_story(db, "Mara starts the rebellion.")
    run_reconciliation(session_db, lambda *a, **k: packet(arc()))
    quote = "The rebellion ended with Mara in control."
    rowid = add_story(db, quote)
    completed = arc(status="resolved", quote=quote, rowid=rowid)
    completed["summary"] = "FUTURE_RESOLVED_REBELLION"
    run_reconciliation(session_db, lambda *a, **k: packet(completed, phase="resolution"))
    assert load_arcs(db, "chat", "s1")[0].status == "resolved"
    with write_transaction(db):
        db.execute("UPDATE messages SET content='Mara is still fighting for the gate.' WHERE id=?", (rowid,))
    seen = []
    run_reconciliation(session_db, lambda *a, **k: seen.append(a[2][-1]["content"]) or packet(arc()))
    assert load_arcs(db, "chat", "s1")[0].status == "active"
    assert all("FUTURE_RESOLVED_REBELLION" not in item for item in seen)
    assert load_arcs(db, "chat", "s1")[0].source_revision > old


def test_arcs_created_in_deleted_future_are_removed_on_rewind(session_db):
    _, db, _ = session_db
    add_story(db)
    run_reconciliation(session_db)
    rowid = add_story(db, "Mara hears of a rebellion.")
    run_reconciliation(session_db, lambda *a, **k: packet(arc()))
    assert load_arcs(db, "chat", "s1")
    with write_transaction(db):
        db.execute("DELETE FROM messages WHERE id=?", (rowid,))
    run_reconciliation(session_db, lambda *a, **k: proposal())
    assert load_arcs(db, "chat", "s1") == []


def test_stale_arc_extraction_cannot_overwrite_after_history_changes(session_db):
    _, db, _ = session_db
    add_story(db)
    run_reconciliation(session_db, lambda *a, **k: packet(arc()))
    rowid = add_story(db, "Mara is victorious.")
    prior = load_arc_row(db, "chat", "s1", "rebellion_arc")

    def generate(*args, **kwargs):
        add_story(db, "The victory was a false report.")
        return packet(arc(status="resolved", quote="Mara is victorious.", rowid=rowid))

    run_reconciliation(session_db, generate)
    assert load_arc_row(db, "chat", "s1", "rebellion_arc") == prior


@pytest.mark.parametrize(
    "invalid",
    [
        [arc()] * 9,
        "not a list",
        [arc(status="complete")],
        [arc() | {"open_questions": ["q"] * 9}],
        [arc() | {"summary": "x" * 2001}],
        [arc() | {"related_threads": ["rebellion"] * 9}],
        [arc() | {"importance": "critical"}],
        [arc() | {"evidence": [{"rowid": True, "quote": "yes"}]}],
    ],
)
def test_arc_parser_limits_are_explicit(invalid):
    with pytest.raises(ValueError):
        parse_arc_updates(invalid)


def test_arc_repository_cas_and_ownership_do_not_own_transaction(session_db):
    _, db, _ = session_db
    row = asdict(parse_arc_updates([arc()])[0]) | {"source_revision": 1}
    with pytest.raises(RuntimeError, match="caller-owned transaction"):
        store_arc_row(db, "chat", "s1", row)
    db.execute("BEGIN")
    assert store_arc_row(db, "chat", "s1", row)
    assert not store_arc_row(db, "chat", "s1", row | {"source_revision": 0, "title": "Older"})
    assert db.in_transaction
    db.rollback()
    assert load_arcs(db, "chat", "s1") == []


def test_arc_json_storage_limits_are_validated_before_sql():
    candidate = arc() | {"open_questions": [str(index) + "\x00" * 399 for index in range(8)]}
    with pytest.raises(ValueError, match="encoded"):
        parse_arc_updates([candidate])


def test_arc_prompt_context_stays_bounded_with_many_verbose_arcs(session_db):
    from bridge.narrative_reconciliation import _reconciliation_base, _source_prompt

    _, db, _ = session_db
    add_story(db)
    run_reconciliation(session_db)
    rowid = add_story(db, "Mara waits for the council's answer.")
    with write_transaction(db):
        for index in range(16):
            value = arc() | {
                "arc_id": f"arc_{index}",
                "summary": "x" * 2000,
                "open_questions": [f"{part}:" + "y" * 395 for part in range(8)],
            }
            parsed = asdict(parse_arc_updates([value])[0]) | {"source_revision": 1}
            store_arc_row(db, "chat", "s1", parsed)
    clock = load_narrative_clock(db, "chat", "s1")
    prompt, through = _source_prompt(db, "chat", "s1", _reconciliation_base(db, "chat", "s1", clock), clock, rowid)
    assert through == rowid
    assert len(prompt) <= 28000
    assert "Mara waits for the council's answer." in prompt


def test_exact_quote_from_another_session_is_not_outcome_evidence(session_db):
    _, db, _ = session_db
    add_story(db)
    run_reconciliation(session_db, lambda *a, **k: packet(arc()))
    quote = "The rebellion ended."
    with write_transaction(db):
        foreign = db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('other','other','assistant',?,1)",
            (quote,),
        ).lastrowid
    add_story(db, "Mara still awaits the battle.")
    run_reconciliation(session_db, lambda *a, **k: packet(arc(status="resolved", quote=quote, rowid=foreign)))
    assert load_arcs(db, "chat", "s1")[0].status == "active"
