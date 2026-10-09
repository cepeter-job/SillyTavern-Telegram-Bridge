"""Roll over classified Summary windows only with durable source and reader proof."""

from __future__ import annotations

import json

from settings_test_support import make_test_settings
from test_story_memory_scope import append, scope
from test_story_memory_scope import db as db

from bridge import memory
from bridge.memory_artifact_store import read_summary_block
from bridge.provider_port import ProviderPort

SESSION = {"session_id": "s", "model_id": "m"}
CREDENTIALS = {
    "public_wolf": "Wolf pact remains binding. " + "Wolf pact records each promise and cause. " * 91,
    "private_mira": "Mira alone knows the hidden silver password. " + "Mira's secret must remain restricted. " * 95,
    "public_tower": "The tower bell rings at dusk. " + "The old tower bell and gate must be remembered. " * 89,
}


def classified(value, *, private=False):
    return {
        "text": CREDENTIALS[value] if value in CREDENTIALS else value,
        "visibility": "restricted" if private else "shared",
        "known_by": ["Mira"] if private else [],
    }


def _drive(db, settings, generated):
    calls = []

    def generate(_api_key, _model, messages, **kwargs):
        calls.append(messages)
        return json.dumps(generated, ensure_ascii=False)

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
    return result, calls


def _accept_full_prior(db, tmp_path):
    prior = {
        "blocks": [
            classified("public_wolf"),
            classified("private_mira", private=True),
            classified("public_tower"),
        ]
    }
    assert 9600 < sum(len(b["text"]) for b in prior["blocks"]) < 12000
    settings = make_test_settings(home=tmp_path)
    original_row = append(db, "The wolf pact was signed. Mira privately hid the password at the old tower.")
    accepted, calls = _drive(db, settings, prior)
    assert accepted.covered_until_rowid == original_row and accepted.complete
    assert len(calls) == 1
    return settings, prior, original_row


def test_rollover_archives_exact_accepted_prior_and_continues_canonical_source(db, tmp_path):
    settings, prior, old_row = _accept_full_prior(db, tmp_path)
    next_row = append(db, "At dawn, the new messenger promises to protect the moonstone.")
    updated, calls = _drive(db, settings, {"blocks": [classified("The messenger promises to protect the moonstone.")]})
    assert updated.covered_until_rowid == next_row and updated.complete
    assert len(calls) == 1
    instructions = calls[0][0]["content"]
    assert "archived" in instructions.casefold() and "only" in instructions.casefold()

    archive = db.execute(
        "SELECT through_rowid,source_document_id,blocks_json FROM summary_archive_windows "
        "WHERE chat_id='c' AND session_id='s'"
    ).fetchall()
    assert len(archive) == 1
    assert archive[0][0] == old_row
    assert archive[0][1].startswith("session:")
    assert json.loads(archive[0][2]) == [
        {**block, "text": block["text"].strip(), "known_by": ["mira"] if block["visibility"] == "restricted" else []}
        for block in prior["blocks"]
    ]
    summary, through = memory.get_session_summary(db, "c", "s")
    assert through == next_row and summary == "The messenger promises to protect the moonstone."
    assert db.execute("SELECT count(*) FROM memory_segments WHERE layer='summary' AND valid=1").fetchone()[0] == 2

    mira = read_summary_block(db, scope(db, "Mira"), query="hidden silver password")
    bob = read_summary_block(db, scope(db, "Bob"), query="hidden silver password")
    assert "Mira alone knows the hidden silver password." in mira.text
    assert "Mira alone knows the hidden silver password." not in bob.text
    assert "moonstone" in mira.text and "moonstone" in bob.text

    # A second pass validates every rehydrated archived pointer, not just the initial read.
    from bridge.memory_scope_store import validate_memory_blocks

    assert validate_memory_blocks(db, scope(db, "Bob"), (bob,))[0].text == bob.text


def test_older_canonical_edit_revokes_archive_and_pending_knowledge(db, tmp_path):
    settings, _prior, old_row = _accept_full_prior(db, tmp_path)
    append(db, "New public clue involving the moonstone.")
    _drive(db, settings, {"blocks": [classified("New public moonstone clue.")]})
    assert db.execute("SELECT count(*) FROM summary_archive_windows").fetchone()[0] == 1
    db.execute("UPDATE messages SET content='The old secret never occurred.' WHERE id=?", (old_row,))
    db.commit()
    assert db.execute("SELECT count(*) FROM summary_archive_windows").fetchone()[0] == 0
    assert (
        "hidden silver password" not in read_summary_block(db, scope(db, "Mira"), query="hidden silver password").text
    )


def test_clearing_summary_retires_archived_windows(db, tmp_path):
    settings, _prior, _old = _accept_full_prior(db, tmp_path)
    append(db, "Moonstone clue.")
    _drive(db, settings, {"blocks": [classified("Moonstone is important.")]})
    assert db.execute("SELECT count(*) FROM summary_archive_windows").fetchone()[0] == 1
    memory.clear_session_summary(db, "c", "s")
    assert db.execute("SELECT count(*) FROM summary_archive_windows").fetchone()[0] == 0


def test_unknown_archive_audience_and_digest_fail_closed(db, tmp_path):
    settings, _prior, _old = _accept_full_prior(db, tmp_path)
    append(db, "Moonstone clue.")
    _drive(db, settings, {"blocks": [classified("Moonstone is important.")]})
    db.execute(
        "UPDATE summary_archive_windows SET blocks_json=?",
        (json.dumps([{"text": "PRIVATE_CANARY", "visibility": "shared", "known_by": ["Mira"]}]),),
    )
    db.commit()
    assert "PRIVATE_CANARY" not in read_summary_block(db, scope(db, "Mira"), query="PRIVATE_CANARY").text
    assert "PRIVATE_CANARY" not in read_summary_block(db, scope(db, "Bob"), query="PRIVATE_CANARY").text


def test_failed_rollover_keeps_old_summary_and_no_archive(db, tmp_path):
    settings, prior, original = _accept_full_prior(db, tmp_path)
    append(db, "New clue.")
    long = {"blocks": [classified("X" * 4500), classified("Y" * 4500), classified("Z" * 4500)]}
    result, _ = _drive(db, settings, long)
    assert not result.complete and result.covered_until_rowid == original
    assert db.execute("SELECT count(*) FROM summary_archive_windows").fetchone()[0] == 0
    summary, through = memory.get_session_summary(db, "c", "s")
    assert through == original and prior["blocks"][0]["text"].strip() in summary


def test_archive_is_not_visible_for_historical_reads_before_publication(db, tmp_path):
    settings, _prior, old = _accept_full_prior(db, tmp_path)
    append(db, "An old oath will be recalled later.")
    _drive(db, settings, {"blocks": [classified("The oath is renewed.")]})
    future = read_summary_block(db, scope(db, "Mira"), query="hidden silver password")
    past = read_summary_block(db, scope(db, "Mira", through=old), query="hidden silver password")
    assert "Mira alone knows" in future.text
    # Earlier accepted facts were already established at that historical cutoff.
    assert "Mira alone knows" in past.text
    before_first = read_summary_block(db, scope(db, "Mira", through=old - 1), query="hidden silver password")
    assert "Mira alone knows" not in before_first.text


def test_session_delete_cleans_archives(db, tmp_path):
    settings, _prior, _old = _accept_full_prior(db, tmp_path)
    append(db, "Moonstone clue.")
    _drive(db, settings, {"blocks": [classified("Moonstone is important.")]})
    assert db.execute("SELECT count(*) FROM summary_archive_windows").fetchone()[0] == 1
    db.execute("DELETE FROM sessions WHERE chat_id='c' AND session_id='s'")
    db.commit()
    assert db.execute("SELECT count(*) FROM summary_archive_windows").fetchone()[0] == 0


def test_multiple_window_rollovers_retain_old_restricted_audiences(db, tmp_path):
    settings, _prior, old_row = _accept_full_prior(db, tmp_path)
    middle = append(db, "The messenger carries a unique bright-moon token.")
    window_two = {
        "blocks": [
            classified("The bright-moon token belongs to the messenger."),
            classified("The messenger's new lantern is copper. " + "A copper lantern is a visible clue. " * 127),
            classified(
                "The copper lantern has a broken handle. " + "The lantern handle is an established public fact. " * 85
            ),
            classified("The second archway is watched. " + "A watchtower remembers the old gate. " * 47),
        ]
    }
    assert sum(len(x["text"]) for x in window_two["blocks"]) >= 9600
    accepted, _ = _drive(db, settings, window_two)
    assert accepted.covered_until_rowid == middle
    later = append(db, "A later turn reveals the glass compass is broken.")
    again, calls = _drive(db, settings, {"blocks": [classified("The glass compass is broken.")]})
    assert again.complete and again.covered_until_rowid == later
    assert len(calls) == 1
    throughs = db.execute(
        "SELECT through_rowid FROM summary_archive_windows WHERE chat_id='c' AND session_id='s' ORDER BY through_rowid"
    ).fetchall()
    assert throughs == [(old_row,), (middle,)]
    assert (
        "Mira alone knows the hidden silver password."
        in read_summary_block(db, scope(db, "Mira"), query="hidden silver password").text
    )
    assert "bright-moon token" in read_summary_block(db, scope(db, "Bob"), query="bright-moon token").text
    assert (
        "Mira alone knows the hidden silver password."
        not in read_summary_block(db, scope(db, "Bob"), query="hidden silver password").text
    )
    assert "glass compass is broken" in read_summary_block(db, scope(db, "Bob")).text


def test_split_canonical_row_archives_only_on_completed_row(db, tmp_path):
    settings, _prior, old_row = _accept_full_prior(db, tmp_path)
    long_text = "CANYON_HEAD/" + "A" * 11990 + "CANYON_TAIL/" + "B" * 450
    new_row = append(db, long_text)
    submitted = []

    def generate(_api, _model, messages, **kwargs):
        submitted.append(messages[-1]["content"])
        if len(submitted) == 1:
            result = {"blocks": [classified("CANYON_HEAD is a landmark.")]}
        else:
            result = {
                "blocks": [
                    classified("CANYON_HEAD is a landmark."),
                    classified("CANYON_TAIL ends the source."),
                ]
            }
        return json.dumps(result)

    def run():
        return memory.generate_session_summary_result(
            db,
            "c",
            SESSION,
            force=True,
            durable=True,
            max_segments=1,
            provider_port=ProviderPort(generate),
            app_settings=settings,
        )

    first = run()
    assert not first.complete and first.covered_until_rowid == old_row
    assert db.execute("SELECT count(*) FROM summary_archive_windows").fetchone()[0] == 0
    assert memory.get_session_summary(db, "c", "s")[1] == old_row
    second = run()
    assert second.complete and second.covered_until_rowid == new_row
    assert db.execute("SELECT count(*) FROM summary_archive_windows").fetchone()[0] == 1
    assert "CANYON_HEAD" in memory.get_session_summary(db, "c", "s")[0]
    assert "CANYON_TAIL" in memory.get_session_summary(db, "c", "s")[0]
    assert db.execute("SELECT count(*) FROM memory_segments WHERE layer='summary' AND valid=1").fetchone()[0] == 3


def test_missing_checkpoint_prevents_archive_and_rollover_publication(db, tmp_path):
    settings, _prior, old_row = _accept_full_prior(db, tmp_path)
    db.execute(
        "DELETE FROM memory_layer_checkpoints WHERE chat_id='c' AND session_id='s' AND layer='summary' "
        "AND through_id=?",
        (old_row,),
    )
    db.commit()
    append(db, "A new pledge from the messenger.")
    outcome, _calls = _drive(db, settings, {"blocks": [classified("The messenger pledges to help.")]})
    assert not outcome.complete and outcome.covered_until_rowid == old_row
    assert db.execute("SELECT count(*) FROM summary_archive_windows").fetchone()[0] == 0
    assert (
        db.execute("SELECT covered_until_rowid FROM session_summaries WHERE chat_id='c' AND session_id='s'").fetchone()[
            0
        ]
        == old_row
    )


def test_manual_summary_override_revokes_older_archival_authority(db, tmp_path):
    settings, _prior, _old = _accept_full_prior(db, tmp_path)
    next_id = append(db, "A moonstone clue.")
    _drive(db, settings, {"blocks": [classified("The moonstone has a crack.")]})
    assert db.execute("SELECT count(*) FROM summary_archive_windows").fetchone()[0] == 1
    from bridge.miniapp_memory_repository import store_summary
    from bridge.sqlite_store import write_transaction

    with write_transaction(db):
        store_summary(db, "c", "s", "Manual replacement only", next_id, 5.0)
    assert db.execute("SELECT count(*) FROM summary_archive_windows").fetchone()[0] == 0
    assert (
        "hidden silver password" not in read_summary_block(db, scope(db, "Mira"), query="hidden silver password").text
    )


def test_archive_from_other_session_never_appears_in_active_story(db, tmp_path):
    settings, _prior, _old = _accept_full_prior(db, tmp_path)
    append(db, "Moonstone clue.")
    _drive(db, settings, {"blocks": [classified("The moonstone is important.")]})
    from bridge.memory_scope_store import resolve_memory_scope

    other = resolve_memory_scope(
        db,
        "c",
        {"session_id": "other"},
        {"name": "Mira"},
    )
    assert other is not None
    assert "Mira alone knows" not in read_summary_block(db, other, query="hidden silver password").text


def test_archived_source_is_rehydrated_into_actual_prompt_context(db, tmp_path):
    settings, _prior, _old = _accept_full_prior(db, tmp_path)
    append(db, "The moonstone is now important.")
    _drive(db, settings, {"blocks": [classified("The moonstone is important.")]})
    from dataclasses import replace

    from test_story_memory_artifacts import service

    runtime = replace(service(), scoped_summary_query=read_summary_block)
    mira = runtime.prompt_context(db, "c", {"session_id": "s"}, {"name": "Mira"}, "hidden silver password")
    bob = runtime.prompt_context(db, "c", {"session_id": "s"}, {"name": "Bob"}, "hidden silver password")
    assert "Mira alone knows the hidden silver password." in mira.summary
    assert "Mira alone knows the hidden silver password." not in bob.summary
    assert "moonstone is important" in mira.summary and "moonstone is important" in bob.summary
    assert mira.evidence and all("PRIVATE_CANARY" not in block.text for block in mira.baseline_blocks)
