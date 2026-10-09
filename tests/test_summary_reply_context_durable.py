"""Durable acceptance of short canonical source replies with reference-only context."""

from __future__ import annotations

import json

from settings_test_support import make_test_settings
from test_story_memory_scope import db as db
from test_summary_reply_context import SESSION, put

from bridge import memory
from bridge.provider_port import ProviderPort


def test_durable_acceptance_credits_only_the_short_canonical_reply(db, tmp_path):
    from bridge.memory_fact_store import load_source
    from bridge.memory_store import source_is_valid

    settings = make_test_settings(home=tmp_path)
    put(db, "user", "Will we keep the compass safe together?")
    proposal = put(db, "assistant", "I promise to protect the compass until dawn.")
    recorded = []

    def generate(_key, _model, request, **_kwargs):
        prompt = request[-1]["content"]
        previous_text = prompt.split("Previous classified summary:\n", 1)[1].split("\nSource role:", 1)[0]
        prior = json.loads(previous_text)
        assert "reference_dialogue" not in prompt
        recorded.append(prompt)
        source_text = prompt.split("Canonical source part:\n", 1)[1]
        return json.dumps(
            {
                "blocks": [
                    *prior.get("blocks", []),
                    {"text": f"Established: {source_text}", "visibility": "shared", "known_by": []},
                ]
            }
        )

    previous = memory.generate_session_summary_result(
        db,
        "c",
        SESSION,
        force=True,
        durable=True,
        max_segments=2,
        provider_port=ProviderPort(generate),
        app_settings=settings,
    )
    assert previous.covered_until_rowid == proposal and previous.complete
    assert len(recorded) == 2
    confirmed = put(db, "user", "Yes")
    evidence = []

    def confirm(_key, _model, request, **_kwargs):
        text = request[-1]["content"]
        evidence.append(text)
        assert "reference_dialogue" in text
        assert text.endswith("Canonical source part:\nYes")
        prior = json.loads(text.split("Previous classified summary:\n", 1)[1].split("\nSource role:", 1)[0])
        return json.dumps(
            {
                "blocks": [
                    *prior.get("blocks", []),
                    {
                        "text": "The user confirmed the promise to protect the compass.",
                        "visibility": "shared",
                        "known_by": [],
                    },
                ]
            }
        )

    accepted = memory.generate_session_summary_result(
        db,
        "c",
        SESSION,
        force=True,
        durable=True,
        max_segments=1,
        provider_port=ProviderPort(confirm),
        app_settings=settings,
    )
    assert accepted.covered_until_rowid == confirmed and accepted.complete
    assert len(evidence) == 1
    checkpoint = db.execute(
        "SELECT source_document_id FROM memory_layer_checkpoints "
        "WHERE chat_id='c' AND session_id='s' AND layer='summary' AND through_id=?",
        (confirmed,),
    ).fetchone()
    assert checkpoint is not None
    source = load_source(db, checkpoint[0])
    assert source is not None and source_is_valid(db, source)
    assert source.content == "Yes" and source.end_id == confirmed
    assert db.execute("SELECT COUNT(*) FROM summary_archive_windows").fetchone() == (0,)
