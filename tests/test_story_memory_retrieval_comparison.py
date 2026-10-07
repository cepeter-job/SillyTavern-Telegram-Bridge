"""The comparison corpus must preserve accepted source and scope contracts."""

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


def test_fixture_annotations_validate():
    from story_memory_retrieval_fixture import load_fixture, validate_fixture

    fixture = load_fixture()
    assert len(fixture["facts"]) == 42
    assert len(fixture["queries"]) == 24
    assert len(fixture["embedding_input_plan"]["inputs"]) == 66
    for mutate in ("unknown", "duplicate", "text", "scope"):
        bad = copy.deepcopy(fixture)
        if mutate == "unknown":
            bad["queries"][0]["judgments"]["relevant_fact_keys"] = ["missing"]
        elif mutate == "duplicate":
            bad["facts"][1]["key"] = "M01"
        elif mutate == "text":
            bad["embedding_input_plan"]["inputs"][0]["text"] = "An answer hint"
        else:
            bad["queries"][0]["scope"]["session_key"] = "unknown"
        with pytest.raises(ValueError):
            validate_fixture(bad)


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    from story_memory_retrieval_corpus import RetrievalCorpus

    with RetrievalCorpus(tmp_path_factory.mktemp("retrieval-corpus")) as built:
        yield built


def test_corpus_comes_from_accepted_sources(corpus):
    from bridge.memory_fact_store import load_source

    assert len(corpus.facts) == 42
    assert len({stored.memory_id for stored in corpus.facts.values()}) == 42
    assert corpus.runtime.unexpected_io == []
    for key, stored in corpus.facts.items():
        assert stored.provenance_kind == "transcript_part"
        source = load_source(corpus.db, stored.evidence.source_document_id)
        assert source is not None
        assert source.role == "user"
        assert stored.fact.summary in source.content
        assert stored.fact.importance == 0.9
        if key.startswith("M"):
            assert stored.evidence.source_start_rowid == corpus.events[f"fact.{key}.user"]["row_id"]
    assert len(corpus.runtime.transport.extraction_sources) >= 58
    assert not any(call["kind"] == "retain" for call in corpus.runtime.transport.calls)


def test_full_eligible_corpus_excludes_forbidden_scopes(corpus):
    cases = {case.query_id: case for case in corpus.cases}
    expected = {"Q13": 20, "Q14": 21, "Q17": 13, "Q18": 14, "Q19": 14, "Q20": 15, "Q22": 19}
    for key, count in expected.items():
        case = cases[key]
        assert len(corpus.eligible(case)) == count
        assert set(case.relevant_fact_keys) <= set(corpus.eligible(case))
        assert not set(case.forbidden_fact_keys) & set(corpus.eligible(case))
    assert corpus.scopes["Q17"].through_rowid == corpus.events["fact.M16.assistant"]["row_id"]
    assert corpus.scopes["Q20"].through_rowid == corpus.events["fact.M15.assistant"]["row_id"]
    assert corpus.scopes["Q17"].through_rowid < corpus.events["closure.prelude.assistant"]["row_id"]


def test_branch_isolation(corpus):
    from bridge.ending_service import load_ending_state
    from bridge.narrative_repository import load_narrative_state_row

    assert corpus.branch.applied and corpus.branch.memory_status == "ready"
    assert len(corpus.checkpoint.payload["memory"]["episodic"]) == 16
    assert corpus.branch_prefix_count == 16
    assert load_ending_state(corpus.db, "evaluation", "main").lifecycle == "closed"
    assert load_ending_state(corpus.db, "evaluation", corpus.sessions["alternate"]["session_id"]).lifecycle == "open"
    assert load_narrative_state_row(corpus.db, "evaluation", "main")["story_phase"] == "closed"
    assert corpus.main_transcript_before_branch == corpus.transcript("main")
    assert corpus.ending_result.completed and corpus.ending_result.delivered
    assert corpus.transcript("main")[-1][2] == "*Rowan closes the harbor ledger.*"
    for number in range(1, 17):
        original, clone = corpus.facts[f"M{number:02}"], corpus.facts[f"A{number:02}"]
        assert clone.memory_id != original.memory_id
        assert clone.session_created_at != original.session_created_at
        assert clone.evidence.source_document_id != original.evidence.source_document_id
        assert clone.evidence.source_start_rowid != original.evidence.source_start_rowid
        assert clone.payload_digest == original.payload_digest
    assert "FACT" not in json.dumps({k: v for k, v in corpus.events.items() if k.startswith("closure.")})
