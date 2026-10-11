"""Recover format failures without changing canon or weakening publication guards."""

import json

import pytest
from test_memory_completion_safety import session_db as session_db
from test_memory_json_transport import routed_provider
from test_narrative_arcs import arc, packet
from test_narrative_reconciliation import add_story, proposal, run_reconciliation

from bridge.diagnostic_logging import DiagnosticFormatter
from bridge.narrative_extraction import MAX_RECONCILIATION_OUTPUT, parse_narrative_extraction
from bridge.narrative_reconciliation import reconcile_narrative_state_now
from bridge.narrative_repository import load_narrative_clock
from bridge.provider_completion import StructuredOutputCapped
from bridge.sqlite_store import write_transaction


def diagnostics(caplog):
    return [json.loads(DiagnosticFormatter().format(record)) for record in caplog.records]


def snapshot(db):
    return {
        table: db.execute("SELECT * FROM " + table).fetchall()  # noqa: S608 -- fixed synthetic table allowlist
        for table in (
            "messages",
            "narrative_state",
            "narrative_scenes",
            "narrative_threads",
            "narrative_arcs",
            "narrative_checkpoints",
            "ending_state",
        )
    }


@pytest.mark.parametrize("fence", ["```json\n", "```\n"])
def test_one_complete_fence_preserves_the_exact_extraction(fence):
    raw = proposal()
    assert parse_narrative_extraction(fence + raw + "\n```") == parse_narrative_extraction(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "Explanation\n" + proposal(),
        "```json\n" + proposal(),
        proposal() + "\n" + proposal(),
        "```json\n" + " " * MAX_RECONCILIATION_OUTPUT + proposal() + "\n```",
    ],
)
def test_fence_support_never_accepts_prose_partial_multiple_or_oversized_output(raw):
    with pytest.raises(ValueError):
        parse_narrative_extraction(raw)


@pytest.mark.parametrize("bad", ['{"PRIVATE_RESPONSE_CANARY":', "[]", '{"schema_version":2}'])
def test_format_recovery_reextracts_once_from_unchanged_canonical_input(session_db, bad):
    _, db, _ = session_db
    rowid = add_story(db, "PRIVATE_SOURCE_CANARY: Mara waits at the gate.")
    before = db.execute("SELECT * FROM messages").fetchall()
    calls = []

    def generate(*args, **kwargs):
        assert not db.in_transaction
        calls.append((args, kwargs))
        return bad if len(calls) == 1 else proposal()

    state = run_reconciliation(session_db, generate)
    assert state.updated_through_rowid == rowid
    assert len(calls) == 2
    initial, repaired = calls[0][0][2], calls[1][0][2]
    assert repaired[-len(initial) :] == initial
    assert "PRIVATE_RESPONSE_CANARY" not in json.dumps(repaired)
    assert all(message["role"] != "assistant" for message in repaired)
    assert all(call[1]["settings"]["json_once"] is True for call in calls)
    assert all(call[1]["force_non_stream"] is True for call in calls)
    assert calls[0][1]["settings"]["max_tokens"] == calls[1][1]["settings"]["max_tokens"] == 2400
    assert db.execute("SELECT * FROM messages").fetchall() == before
    assert db.execute("SELECT COUNT(*) FROM narrative_checkpoints").fetchone()[0] == 1


def test_repeated_format_failure_is_bounded_private_and_atomic(session_db, caplog):
    _, db, _ = session_db
    add_story(db)
    run_reconciliation(session_db)
    add_story(db, "PRIVATE_SOURCE_CANARY")
    before = snapshot(db)
    calls = []

    def generate(*args, **kwargs):
        calls.append((args, kwargs))
        return '{"PRIVATE_RESPONSE_CANARY":'

    run_reconciliation(session_db, generate)
    assert len(calls) == 2
    assert snapshot(db) == before
    assert any(record.get("reason") == "malformed_json" for record in diagnostics(caplog))
    assert any(record.get("phase") == "parse" for record in diagnostics(caplog))
    assert "PRIVATE_SOURCE_CANARY" not in caplog.text
    assert "PRIVATE_RESPONSE_CANARY" not in caplog.text
    assert "Expecting" not in caplog.text
    record = diagnostics(caplog)[-1]
    assert record["event"] == "narrative.reconciliation_rejected"
    assert record["attempt"] == 2
    assert record["revision"] == load_narrative_clock(db, "chat", "s1")["state_revision"]
    assert record["source_message_id"] == load_narrative_clock(db, "chat", "s1")["latest_rowid"]
    assert "PRIVATE_" not in json.dumps(diagnostics(caplog))


def test_shape_failure_has_a_content_free_distinct_diagnostic(session_db, caplog):
    _, db, _ = session_db
    add_story(db)
    before = snapshot(db)
    run_reconciliation(session_db, lambda *a, **k: "[]")
    assert snapshot(db) == before
    assert any(record.get("reason") == "invalid_shape" for record in diagnostics(caplog))


@pytest.mark.parametrize("error", [RuntimeError("PRIVATE_EXCEPTION_CANARY"), ValueError("PRIVATE_EXCEPTION_CANARY")])
def test_provider_errors_are_never_format_repaired_or_logged_verbatim(session_db, caplog, error):
    _, db, _ = session_db
    add_story(db)
    before = snapshot(db)
    calls = []

    def generate(*args, **kwargs):
        calls.append(True)
        raise error

    run_reconciliation(session_db, generate)
    assert len(calls) == 1
    assert snapshot(db) == before
    assert any(record.get("reason") == "provider_failed" for record in diagnostics(caplog))
    assert "PRIVATE_EXCEPTION_CANARY" not in caplog.text


@pytest.mark.parametrize("when", ["before_repair", "during_repair"])
def test_source_revision_change_suppresses_retry_or_rejects_its_publication(session_db, when):
    _, db, _ = session_db
    add_story(db)
    calls = []

    def generate(*args, **kwargs):
        calls.append(True)
        if (when == "before_repair" and len(calls) == 1) or (when == "during_repair" and len(calls) == 2):
            add_story(db, "A newer committed fact.")
        return '{"schema_version":' if len(calls) == 1 else proposal()

    state = run_reconciliation(session_db, generate)
    assert len(calls) == (1 if when == "before_repair" else 2)
    assert state.updated_through_rowid == 0
    assert load_narrative_clock(db, "chat", "s1")["updated_through_rowid"] == 0
    assert db.execute("SELECT * FROM narrative_threads").fetchall() == []
    assert db.execute("SELECT * FROM narrative_checkpoints").fetchall() == []


@pytest.mark.parametrize("raw", ["", proposal()])
def test_explicit_output_cap_is_reextracted_even_when_partial_json_parses(session_db, raw, caplog):
    _, db, _ = session_db
    rowid = add_story(db)
    calls = []

    def generate(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise StructuredOutputCapped(raw)
        return proposal()

    state = run_reconciliation(session_db, generate)
    assert state.updated_through_rowid == rowid
    assert len(calls) == 2
    assert any(record.get("reason") == "output_capped" for record in diagnostics(caplog))


@pytest.mark.parametrize("provider", ["nano-gpt", "openrouter"])
def test_reconciliation_uses_json_transport_without_story_continuations(session_db, monkeypatch, tmp_path, provider):
    settings, db, session = session_db
    rowid = add_story(db)
    payloads = [
        {"choices": [{"message": {"content": '{"schema_version":'}, "finish_reason": "length"}]},
        {"choices": [{"message": {"content": proposal()}, "finish_reason": "stop"}]},
    ]
    port, sent = routed_provider(
        tmp_path,
        monkeypatch,
        payloads,
        provider=provider,
        host="nano-gpt.com" if provider == "nano-gpt" else "openrouter.ai",
        model_id="z-ai/glm-5.2" if provider == "nano-gpt" else "test/model",
    )
    state = reconcile_narrative_state_now(db, "", "chat", session, provider_port=port, app_settings=settings)
    assert state.updated_through_rowid == rowid
    assert len(sent) == 2
    assert sent[1]["messages"][-len(sent[0]["messages"]) :] == sent[0]["messages"]
    assert not any(message["role"] == "assistant" for request in sent for message in request["messages"])
    if provider == "nano-gpt":
        assert all(request["response_format"] == {"type": "json_object"} for request in sent)
    else:
        assert all("response_format" not in request for request in sent)


@pytest.mark.parametrize("bad", ["invented", "foreign", "old", "future", "missing", "malformed"])
def test_evidence_rejections_are_never_repaired_or_published(session_db, caplog, bad):
    _, db, _ = session_db
    old = add_story(db, "An earlier committed fact.")
    run_reconciliation(session_db, lambda *a, **k: packet(arc()))
    rowid = add_story(db, "Mara waits at the gate.")
    candidate = arc(status="resolved", quote="Mara waits at the gate.", rowid=rowid)
    if bad == "invented":
        candidate["evidence"][0]["quote"] = "PRIVATE_INVENTED_CANARY"
    elif bad == "foreign":
        with write_transaction(db):
            foreign = db.execute(
                "INSERT INTO messages(chat_id,session_id,role,content,created_at) "
                "VALUES('other','other','assistant','Mara waits at the gate.',1)"
            ).lastrowid
        candidate["evidence"][0]["rowid"] = foreign
    elif bad == "old":
        candidate["evidence"] = [{"rowid": old, "quote": "An earlier committed fact."}]
    elif bad == "future":
        candidate["evidence"][0]["rowid"] = rowid + 100
    elif bad == "missing":
        candidate["evidence"] = []
    else:
        candidate["evidence"][0]["rowid"] = True
    before = snapshot(db)
    calls = []

    def generate(*args, **kwargs):
        calls.append(True)
        return packet(candidate, phase="resolution")

    run_reconciliation(session_db, generate)
    assert len(calls) == 1
    assert snapshot(db) == before
    assert any(record.get("reason") == "invalid_evidence" for record in diagnostics(caplog))
    assert "PRIVATE_INVENTED_CANARY" not in caplog.text


def test_completed_scene_identifier_reuse_is_not_repaired(session_db, caplog):
    _, db, _ = session_db
    add_story(db)
    run_reconciliation(session_db)
    add_story(db, "Mara leaves the gate.")
    run_reconciliation(session_db, lambda *a, **k: proposal("tower", transition="cut"))
    add_story(db, "Mara returns to the gate.")
    before = snapshot(db)
    calls = []
    run_reconciliation(session_db, lambda *a, **k: calls.append(True) or proposal("gate", transition="cut"))
    assert len(calls) == 1
    assert snapshot(db) == before
    assert any(record.get("phase") == "publish" for record in diagnostics(caplog))
    assert any(record.get("reason") == "state_rejected" for record in diagnostics(caplog))


@pytest.mark.parametrize("collection", ["evidence", "open_questions", "related_threads"])
def test_encoded_arc_collection_overflow_is_never_repaired(session_db, caplog, collection):
    _, db, _ = session_db
    rowid = add_story(db)
    candidate = arc()
    if collection == "evidence":
        candidate[collection] = [{"rowid": rowid, "quote": str(index) + "\x00" * 499} for index in range(4)]
    else:
        length = 399 if collection == "open_questions" else 99
        candidate[collection] = [str(index) + "\x00" * length for index in range(8)]
    before = snapshot(db)
    calls = []
    run_reconciliation(session_db, lambda *a, **k: calls.append(True) or packet(candidate))
    assert len(calls) == 1
    assert snapshot(db) == before
    assert any(record.get("reason") == "invalid_collection" for record in diagnostics(caplog))


def test_reconciliation_prompt_describes_parser_bounds(session_db):
    _, db, _ = session_db
    add_story(db)
    instructions = []
    run_reconciliation(session_db, lambda *a, **k: instructions.append(a[2][0]["content"]) or proposal())
    instruction = instructions[0]
    for required in (
        "24000",
        "100 characters",
        "alphanumeric",
        "200 characters",
        "1000 characters",
        "2000 characters",
        "400 characters",
        "4000",
        "4096",
        "duplicate",
        "1–4",
        "500 characters",
    ):
        assert required in instruction
    assert len(instruction) < 4200
