"""Audience output formatting for classified Summary; never auto-repair privacy conflicts."""

import json

import pytest
from settings_test_support import make_test_settings
from test_story_memory_scope import append
from test_story_memory_scope import db as db

from bridge import memory
from bridge.memory_store import next_source_segment
from bridge.provider_port import ProviderPort

SESSION = {"session_id": "s", "model_id": "m"}
PUBLIC = {"text": "The bell rings in the square.", "visibility": "shared", "known_by": []}
PRIVATE = {"text": "Mira privately hides the silver key.", "visibility": "restricted", "known_by": ["Mira"]}
RESPONSE = json.dumps({"blocks": [PUBLIC, PRIVATE]})


def _canonical(db):
    append(
        db,
        "All townspeople heard the bell. Mira privately planned to hide the silver key; "
        "other characters did not learn her plan.",
    )
    source = next_source_segment(db, "c", "s", "summary")
    assert source is not None
    return source


def test_summary_output_contract_explains_both_exclusive_audience_shapes(db, tmp_path):
    source = _canonical(db)
    calls = []

    def generate(_key, _model, messages, **_kwargs):
        calls.append(messages)
        return RESPONSE

    result = memory.extract_summary_segment(
        db,
        "c",
        SESSION,
        {"blocks": []},
        source,
        provider_port=ProviderPort(generate),
        app_settings=make_test_settings(home=tmp_path),
    )
    assert result["blocks"] == [
        PUBLIC,
        {**PRIVATE, "known_by": ["mira"]},
    ]
    assert len(calls) == 1
    system = calls[0][0]["content"]
    assert '"visibility":"shared","known_by":[]' in system
    assert '"visibility":"restricted","known_by":[' in system
    assert "A nonempty known_by MUST use restricted" in system
    assert "even when its text names people" in system
    assert "source-supported" in system
    assert "before sending JSON" in system
    assert "syntax only" in system
    assert calls[0][-1]["content"].endswith(source.content)


def test_summary_single_format_repair_preserves_audience_contract_and_source(db, tmp_path):
    source = _canonical(db)
    calls = []

    def generate(_key, _model, messages, **_kwargs):
        calls.append(messages)
        return "{truncated" if len(calls) == 1 else RESPONSE

    result = memory.extract_summary_segment(
        db,
        "c",
        SESSION,
        {"blocks": []},
        source,
        provider_port=ProviderPort(generate),
        app_settings=make_test_settings(home=tmp_path),
    )
    assert result["blocks"][0] == PUBLIC
    assert result["blocks"][1]["visibility"] == "restricted"
    assert len(calls) == 2
    assert calls[1][1:] == calls[0], "Repair must keep the exact original canonical source"
    assert "A nonempty known_by MUST use restricted" in calls[1][0]["content"]
    assert '"visibility":"shared","known_by":[]' in calls[1][0]["content"]
    assert "{truncated" not in json.dumps(calls[1]), "Rejected output must not become input"


def test_conflicting_audience_still_fails_closed_without_repair_or_coverage(db, tmp_path):
    source = _canonical(db)
    calls = []

    def generate(_key, _model, messages, **_kwargs):
        calls.append(messages)
        return json.dumps(
            {"blocks": [{"text": "Mira privately hides the silver key.", "visibility": "shared", "known_by": ["Mira"]}]}
        )

    with pytest.raises(ValueError, match="Shared memory cannot carry a conflicting restricted audience"):
        memory.extract_summary_segment(
            db,
            "c",
            SESSION,
            {"blocks": []},
            source,
            provider_port=ProviderPort(generate),
            app_settings=make_test_settings(home=tmp_path),
        )
    assert len(calls) == 1, "Privacy conflicts are never repaired by a second call"
    assert db.execute("SELECT COUNT(*) FROM summary_archive_windows").fetchone() == (0,)
    assert db.execute("SELECT COALESCE(MAX(covered_until_rowid),0) FROM session_summaries").fetchone() == (0,)


def test_small_summary_format_contract_includes_strict_total_block_cap():
    from bridge.summary_block_coalescing import summary_length_contract

    instruction = summary_length_contract({"blocks": []})
    assert "no more than 32" in instruction
    assert "12,000" in instruction
    assert "preserve" in instruction.lower() or "distinct" in instruction.lower()


def test_oversized_block_inventory_receives_explicit_cap_in_single_repair(db, tmp_path):
    source = _canonical(db)
    calls = []
    # Alternating audience blocks cannot be losslessly coalesced across
    # restricted/public boundaries; the current validator must reject them.
    oversized = {
        "blocks": [
            {
                "text": f"Source fact {n}.",
                "visibility": "shared" if n % 2 == 0 else "restricted",
                "known_by": [] if n % 2 == 0 else ["Mira"],
            }
            for n in range(33)
        ]
    }

    def generate(_key, _model, messages, **_kwargs):
        calls.append(messages)
        return json.dumps(oversized) if len(calls) == 1 else RESPONSE

    accepted = memory.extract_summary_segment(
        db,
        "c",
        SESSION,
        {"blocks": []},
        source,
        provider_port=ProviderPort(generate),
        app_settings=make_test_settings(home=tmp_path),
    )
    assert len(calls) == 2
    assert len(accepted["blocks"]) == 2
    assert "Use at most 32 blocks" in calls[0][0]["content"]
    assert "no more than 32" in calls[1][0]["content"]
    assert "12,000" in calls[1][0]["content"]
    assert calls[1][1:] == calls[0]
    assert "Source fact 0" not in json.dumps(calls[1])


def test_fresh_summary_rollover_uses_new_window_token_budget(db, tmp_path):
    from test_summary_window_archive import _accept_full_prior

    settings, prior, _old_row = _accept_full_prior(db, tmp_path)
    assert memory.summary_output_budget(prior) == 4096
    new_id = append(db, "After the archiving boundary, the messenger discovers a bronze compass.")
    seen = []

    def generate(_api, _model, messages, **kwargs):
        seen.append((kwargs["settings"]["max_tokens"], messages))
        return json.dumps(
            {"blocks": [{"text": "A bronze compass was discovered.", "visibility": "shared", "known_by": []}]}
        )

    result = memory.generate_session_summary_result(
        db,
        "c",
        SESSION,
        force=True,
        durable=True,
        max_segments=1,
        provider_port=ProviderPort(generate),
        app_settings=settings,
    )
    assert result.complete and result.covered_until_rowid == new_id
    assert len(seen) == 1
    assert seen[0][0] == memory.summary_output_budget({"blocks": []})
    assert seen[0][0] < 4096
    assert db.execute("SELECT COUNT(*) FROM summary_archive_windows").fetchone() == (1,)
