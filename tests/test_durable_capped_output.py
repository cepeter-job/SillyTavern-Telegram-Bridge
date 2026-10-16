"""Capped helper/curator output fails before SQL publication or coverage."""

import pytest
from memory_runtime_test_support import isolated_memory_runtime as isolated_memory_runtime
from session_test_support import session_db as session_db
from test_durable_memory_workers import add
from test_tracker_output_contracts import nanogpt_port, response

from bridge.memory_curator import get_curated_memory_state
from bridge.memory_store import claim_jobs
from bridge.memory_workers import run_memory_claim


def assert_failed_before_publication(db, layer):
    assert db.execute(
        "SELECT completed_version,lease_token,last_error FROM memory_jobs WHERE layer=?", (layer,)
    ).fetchone() == (0, "", "output_capped")
    assert db.execute(
        "SELECT covered_id,draft_json,draft_source_id FROM memory_layer_state WHERE layer=?", (layer,)
    ).fetchone() == (0, "{}", "")  # Initialized empty draft has no accepted source part.
    for table in ("memory_segments", "memory_layer_checkpoints", "npc_entities", "simulation_sources", "scene_states"):
        assert db.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)  # noqa: S608 -- fixed test table allowlist
    assert get_curated_memory_state(db, "chat", "s1") == ([], 0)


@pytest.mark.parametrize(
    "kind,cap,complete", [("npc", 1400, '{"npcs":[],"simulation":{}}'), ("scene", 1000, '{"state":{},"blocks":[]}')]
)
@pytest.mark.parametrize(
    "initial_empty,repair_empty",
    [(True, True), (False, True), (True, False)],
    ids=["empty-initial", "empty-repair", "complete-looking-repair"],
)
def test_durable_capped_final_failure_never_advances_coverage(
    tmp_path, monkeypatch, session_db, caplog, kind, cap, complete, initial_empty, repair_empty
):
    settings, db, session = session_db
    add(db, "Synthetic event at the library.")
    claim = claim_jobs(db, layers=(kind,))[0]
    port, sent = nanogpt_port(
        tmp_path,
        monkeypatch,
        [
            response(None if initial_empty else complete, "length", cap),
            response("" if repair_empty else complete, "length", cap),
        ],
    )
    result = run_memory_claim(db, claim, session, {"name": "Primary"}, provider_port=port, app_settings=settings)
    assert result == "output_capped"
    assert len(sent) == 2
    assert sent[0]["messages"][-1] == sent[1]["messages"][-1]
    assert all(call["max_tokens"] == cap and call["stream"] is False for call in sent)
    assert_failed_before_publication(db, kind)
    assert "Synthetic event" not in caplog.text


@pytest.mark.parametrize("content", [None, '{"memories":[{"key":"synthetic","text":"Synthetic canary fact"}]}'])
def test_durable_curator_cap_has_no_repair_or_coverage(tmp_path, monkeypatch, session_db, caplog, content):
    settings, db, session = session_db
    add(db, "Synthetic curator source.")
    claim = claim_jobs(db, layers=("curator",))[0]
    port, sent = nanogpt_port(tmp_path, monkeypatch, [response(content, "length", 1400)])
    result = run_memory_claim(db, claim, session, {"name": "Primary"}, provider_port=port, app_settings=settings)
    assert result == "output_capped"
    assert len(sent) == 1 and sent[0]["max_tokens"] == 1400 and sent[0]["stream"] is False
    assert_failed_before_publication(db, "curator")
    assert "Synthetic canary fact" not in caplog.text
