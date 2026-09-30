import json
from dataclasses import replace

import pytest
from test_light_novel_storage import novel_db as novel_db

from bridge.provider_port import ProviderPort
from bridge.sqlite_store import write_transaction


def started(db):
    from bridge.conversation_lifecycle import configure_conversation, conversation_state, mark_started

    configure_conversation(db, "chat", "story", "lightnovel", "b")
    mark_started(db, "chat", "story", conversation_state(db, "chat", "story").epoch)


def story_row(db, text="The door opens."):
    with write_transaction(db):
        return db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('chat','story','assistant',?,1)",
            (text,),
        ).lastrowid


@pytest.mark.parametrize("count", [2, 3, 4])
def test_requested_count_sampled_once_and_preserved(novel_db, count):
    from bridge.light_novel_service import prepare_turn

    db, session, _ = novel_db
    started(db)
    seen = []
    record = prepare_turn(db, "chat", session, "message:4", "owner", rng=lambda choices: seen.append(choices) or count)
    second = prepare_turn(db, "chat", session, "message:4", "owner", rng=lambda _: pytest.fail("resampled"))
    assert second.requested_count == record.requested_count == count
    assert seen == [(2, 3, 4)]


def test_normal_turn_does_not_create_choice_records(novel_db):
    from bridge.light_novel_service import prepare_turn

    db, session, _ = novel_db
    assert prepare_turn(db, "chat", session, "message:4") is None
    assert db.execute("SELECT count(*) FROM light_novel_choice_sets").fetchone()[0] == 0


def test_inline_parser_keeps_story_when_choice_tail_is_invalid():
    from bridge.light_novel_format import parse_story_response

    for source in [
        '{"story":"The door opens.","choices":broken}',
        '{"story":"The door opens."}',
        '{"story":"The door opens.","choices":["Go"]}',
    ]:
        story, choices = parse_story_response(source, 3)
        assert story == "The door opens."
        assert choices is None


def test_inline_parser_recovers_trailing_fenced_envelope_after_prose():
    from bridge.light_novel_format import parse_story_response

    source = """Draft narrative that must not leak.

```json
{"story":"Canonical narrative.","choices":["Open the door","Wait outside"]}
```"""
    assert parse_story_response(source, 2) == (
        "Canonical narrative.",
        ["Open the door", "Wait outside"],
    )


def test_inline_parser_recovers_trailing_story_when_its_choices_are_invalid():
    from bridge.light_novel_format import parse_story_response

    source = """Draft narrative that must not leak.

```json
{"story":"Canonical narrative.","choices":["Only one"]}
```"""
    assert parse_story_response(source, 2) == ("Canonical narrative.", None)


def test_inline_parser_accepts_unambiguous_trailing_choices_only_envelope():
    from bridge.light_novel_format import parse_story_response

    source = """Narrative that remains usable.

```json
{"choices":["Open the door","Wait outside"]}
```"""
    assert parse_story_response(source, 2) == (
        "Narrative that remains usable.",
        ["Open the door", "Wait outside"],
    )


def test_inline_parser_removes_a_trailing_non_object_json_value():
    from bridge.light_novel_format import parse_story_response

    source = """Narrative that remains usable.

```json
["Open the door","Wait outside"]
```"""
    assert parse_story_response(source, 2) == ("Narrative that remains usable.", None)


def test_inline_parser_does_not_recover_an_ambiguous_multi_fence_response():
    from bridge.light_novel_format import parse_story_response

    source = """Narrative with an earlier code block.
```
example
```
```json
{"story":"Other narrative.","choices":["Open the door","Wait outside"]}
```"""
    expected = """Narrative with an earlier code block.
```
example
```"""
    assert parse_story_response(source, 2) == (expected, None)


def test_inline_parser_never_leaks_envelope():
    from bridge.light_novel_format import parse_story_response

    assert parse_story_response('```json\n{"story":"Hello","choices":["Go", "Stay"]}\n```', 2) == (
        "Hello",
        ["Go", "Stay"],
    )
    with pytest.raises(ValueError):
        parse_story_response('{"choices":["Go","Stay"]}', 2)


@pytest.mark.parametrize(
    "values", [["/reset", "Stay"], ["@bot /reset", "Stay"], ["Go", " go "], ["Go"], ["x" * 161, "Stay"], [True, "Stay"]]
)
def test_unsafe_or_invalid_choices_are_rejected(values):
    from bridge.light_novel_format import validate_choices

    with pytest.raises(ValueError):
        validate_choices(values, 2)


def test_mode_a_inline_rejection_logs_reason_and_counts_without_content(novel_db, caplog):
    from bridge.conversation_lifecycle import configure_conversation, conversation_state, mark_started
    from bridge.light_novel_turn import begin_novel_turn

    db, session, _settings = novel_db
    configure_conversation(db, "chat", "story", "lightnovel", "a")
    mark_started(db, "chat", "story", conversation_state(db, "chat", "story").epoch)
    turn = begin_novel_turn(db, "chat", session, "message", 24)
    count = turn.record.requested_count
    wrong_count = 2 if count != 2 else 3
    private_story = "PRIVATE_STORY_CONTENT"
    private_choice = "PRIVATE_CHOICE_CONTENT"
    source = json.dumps({"story": private_story, "choices": [private_choice] * wrong_count})

    with caplog.at_level("WARNING"):
        assert turn.extract(source) == private_story

    assert "Light Novel inline choices unavailable" in caplog.text
    assert "reason=invalid_choice_count" in caplog.text
    assert f"requested_count={count}" in caplog.text
    assert f"observed_count={wrong_count}" in caplog.text
    assert private_story not in caplog.text
    assert private_choice not in caplog.text


@pytest.mark.parametrize("strategy,expected", [("a", "story::test"), ("b", "utility::test"), ("c", "story::test")])
def test_choice_only_strategy_routes_correct_model_outside_transaction(novel_db, strategy, expected):
    from bridge.conversation_lifecycle import configure_conversation, conversation_state, mark_started
    from bridge.light_novel_service import attach_turn, ensure_choices, prepare_turn
    from bridge.model_selection import set_task_model

    db, session, settings = novel_db
    configure_conversation(db, "chat", "story", "lightnovel", strategy)
    mark_started(db, "chat", "story", conversation_state(db, "chat", "story").epoch)
    set_task_model(db, "chat", "story", "utility::test")
    record = prepare_turn(db, "chat", session, "opening:1", "owner", rng=lambda _: 2)
    rowid = story_row(db)
    attach_turn(db, record, rowid, "The door opens.")
    calls = []

    def generate(key, model, messages, **kwargs):
        assert not db.in_transaction
        calls.append((model, messages, kwargs))
        return json.dumps({"choices": ["Go inside", "Wait outside"]})

    result = ensure_choices(
        db, record.nonce, session, {"name": "Alice"}, provider_port=ProviderPort(generate), app_settings=settings
    )
    assert result.choices == ("Go inside", "Wait outside")
    assert calls[0][0] == expected
    assert calls[0][2]["request_timeout"] == 60
    assert calls[0][2]["force_non_stream"]
    ensure_choices(
        db,
        record.nonce,
        session,
        {},
        provider_port=ProviderPort(lambda *a, **k: pytest.fail("already ready")),
        app_settings=settings,
    )


def test_choice_generation_lease_covers_bounded_retry_window(novel_db):
    import time

    from bridge.light_novel_repository import load_choice_set
    from bridge.light_novel_service import attach_turn, ensure_choices, prepare_turn

    db, session, settings = novel_db
    started(db)
    record = prepare_turn(db, "chat", session, "turn-lease", "owner", rng=lambda _: 2)
    attach_turn(db, record, story_row(db), "The door opens.")
    remaining = []

    def generate(*args, **kwargs):
        current = load_choice_set(db, record.nonce)
        remaining.append(current.lease_until - time.time())
        return '{"choices":["Go inside","Wait outside"]}'

    result = ensure_choices(db, record.nonce, session, {}, provider_port=ProviderPort(generate), app_settings=settings)

    assert result.generation_status == "ready"
    assert remaining and remaining[0] > 130


def test_choice_generation_retries_transient_timeout_once(novel_db, caplog):
    from bridge.light_novel_service import attach_turn, ensure_choices, prepare_turn

    db, session, settings = novel_db
    started(db)
    record = prepare_turn(db, "chat", session, "turn-timeout", "owner", rng=lambda _: 2)
    attach_turn(db, record, story_row(db), "The door opens.")
    calls = []

    def generate(*args, **kwargs):
        calls.append(kwargs["request_timeout"])
        if len(calls) == 1:
            raise TimeoutError("provider exceeded deadline")
        return '{"choices":["Go inside","Wait outside"]}'

    with caplog.at_level("WARNING"):
        result = ensure_choices(
            db, record.nonce, session, {}, provider_port=ProviderPort(generate), app_settings=settings
        )

    assert result.generation_status == "ready"
    assert result.choices == ("Go inside", "Wait outside")
    assert calls == [60, 60]
    assert "stage=provider" in caplog.text
    assert "error_type=ProviderRequestError" in caplog.text
    assert "reason=timeout" in caplog.text
    assert "retrying" in caplog.text


def test_choice_generation_retries_empty_response_once(novel_db):
    from bridge.light_novel_service import attach_turn, ensure_choices, prepare_turn

    db, session, settings = novel_db
    started(db)
    record = prepare_turn(db, "chat", session, "turn-empty", "owner", rng=lambda _: 2)
    attach_turn(db, record, story_row(db), "The door opens.")
    responses = iter(["   ", '{"choices":["Go inside","Wait outside"]}'])
    calls = []

    def generate(*args, **kwargs):
        calls.append(kwargs["request_timeout"])
        return next(responses)

    result = ensure_choices(db, record.nonce, session, {}, provider_port=ProviderPort(generate), app_settings=settings)

    assert result.generation_status == "ready"
    assert calls == [60, 60]


def test_choice_generation_retries_transient_http_status_and_hides_url(novel_db, caplog):
    import urllib.error

    from bridge.light_novel_service import attach_turn, ensure_choices, prepare_turn

    db, session, settings = novel_db
    started(db)
    record = prepare_turn(db, "chat", session, "turn-http", "owner", rng=lambda _: 2)
    attach_turn(db, record, story_row(db), "The door opens.")
    calls = []

    def generate(*args, **kwargs):
        calls.append(kwargs["request_timeout"])
        if len(calls) == 1:
            raise urllib.error.HTTPError(
                "https://PRIVATE_PROVIDER_URL.example/secret",
                503,
                "Service Unavailable",
                {},
                None,
            )
        return '{"choices":["Go inside","Wait outside"]}'

    with caplog.at_level("WARNING"):
        result = ensure_choices(
            db, record.nonce, session, {}, provider_port=ProviderPort(generate), app_settings=settings
        )

    assert result.generation_status == "ready"
    assert calls == [60, 60]
    assert "http_status=503" in caplog.text
    assert "reason=provider_unavailable" in caplog.text
    assert "PRIVATE_PROVIDER_URL" not in caplog.text


def test_choice_generation_does_not_retry_parse_failure_and_logs_safely(novel_db, caplog):
    from bridge.light_novel_service import attach_turn, ensure_choices, prepare_turn

    db, session, settings = novel_db
    settings = replace(settings, api_key="SECRET_API_KEY")
    started(db)
    record = prepare_turn(db, "chat", session, "turn-parse", "owner", rng=lambda _: 2)
    attach_turn(db, record, story_row(db, "SECRET_STORY_TEXT"), "SECRET_STORY_TEXT")
    calls = []
    raw = "SECRET_RAW_OUTPUT not-json"

    def generate(*args, **kwargs):
        calls.append(kwargs["request_timeout"])
        return raw

    with caplog.at_level("WARNING"):
        result = ensure_choices(
            db, record.nonce, session, {}, provider_port=ProviderPort(generate), app_settings=settings
        )

    assert result.generation_status == "failed"
    assert calls == [60]
    assert "stage=parse" in caplog.text
    assert "reason=invalid_json" in caplog.text
    assert "requested_count=2" in caplog.text
    assert "model=story::test" in caplog.text
    assert "SECRET_STORY_TEXT" not in caplog.text
    assert "SECRET_RAW_OUTPUT" not in caplog.text
    assert settings.api_key not in caplog.text


def test_choice_generation_does_not_retry_non_transient_provider_error(novel_db, caplog):
    from bridge.light_novel_service import attach_turn, ensure_choices, prepare_turn

    db, session, settings = novel_db
    started(db)
    record = prepare_turn(db, "chat", session, "turn-provider-error", "owner", rng=lambda _: 2)
    attach_turn(db, record, story_row(db), "The door opens.")
    calls = []

    def generate(*args, **kwargs):
        calls.append(kwargs["request_timeout"])
        raise RuntimeError("provider configuration rejected request with PRIVATE_DETAIL")

    with caplog.at_level("WARNING"):
        result = ensure_choices(
            db, record.nonce, session, {}, provider_port=ProviderPort(generate), app_settings=settings
        )

    assert result.generation_status == "failed"
    assert calls == [60]
    assert "stage=provider" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert "reason=provider_error" in caplog.text
    assert "PRIVATE_DETAIL" not in caplog.text


def test_failed_choices_retry_does_not_change_story_or_count(novel_db):
    from bridge.light_novel_service import attach_turn, ensure_choices, prepare_turn

    db, session, settings = novel_db
    started(db)
    record = prepare_turn(db, "chat", session, "turn", "owner", rng=lambda _: 3)
    attach_turn(db, record, story_row(db), "The door opens.")
    bad = ProviderPort(lambda *a, **k: (_ for _ in ()).throw(RuntimeError("provider error")))
    result = ensure_choices(db, record.nonce, session, {}, provider_port=bad, app_settings=settings)
    assert result.generation_status == "failed"
    good = ProviderPort(lambda *a, **k: '{"choices":["Go", "Stay", "Ask"]}')
    result = ensure_choices(db, record.nonce, session, {}, provider_port=good, app_settings=settings, retry=True)
    assert result.requested_count == 3
    assert len(result.choices) == 3
    assert db.execute("SELECT content FROM messages").fetchall() == [("The door opens.",)]


def test_inline_ready_choices_do_not_call_another_model(novel_db):
    from bridge.light_novel_service import attach_turn, ensure_choices, prepare_turn

    db, session, settings = novel_db
    started(db)
    record = prepare_turn(db, "chat", session, "turn", "owner", rng=lambda _: 2)
    attach_turn(db, record, story_row(db), "The door opens.", ["Go", "Stay"])
    result = ensure_choices(
        db,
        record.nonce,
        session,
        {},
        provider_port=ProviderPort(lambda *a, **k: pytest.fail("extra call")),
        app_settings=settings,
    )
    assert result.generation_status == "ready"


def test_reset_during_provider_call_cannot_publish_old_choices(novel_db):
    from bridge.conversation_lifecycle import reset_conversation
    from bridge.light_novel_service import attach_turn, ensure_choices, prepare_turn

    db, session, settings = novel_db
    started(db)
    record = prepare_turn(db, "chat", session, "turn", "owner", rng=lambda _: 2)
    attach_turn(db, record, story_row(db), "The door opens.")

    def generate(*a, **k):
        reset_conversation(db, "chat", "story")
        return '{"choices":["Go", "Stay"]}'

    result = ensure_choices(db, record.nonce, session, {}, provider_port=ProviderPort(generate), app_settings=settings)
    assert result.state == "invalidated"
    assert not result.choices


def test_grounded_user_choice_generation_avoids_assumed_success(novel_db):
    from bridge.conversation_lifecycle import configure_conversation, conversation_state, mark_started
    from bridge.light_novel_service import attach_turn, ensure_choices, prepare_turn
    from bridge.model_selection import set_task_model

    db, session, settings = novel_db
    configure_conversation(db, "chat", "story", "lightnovel", "b")
    mark_started(db, "chat", "story", conversation_state(db, "chat", "story").epoch)
    set_task_model(db, "chat", "story", "utility::test")
    session["grounded_user"] = "on"
    record = prepare_turn(db, "chat", session, "grounded-turn", "owner", rng=lambda _: 2)
    story = "The guard blocks the gate."
    attach_turn(db, record, story_row(db, story), story)
    calls = []

    def generate(*args, **kwargs):
        calls.append(args[2])
        return '{"choices":["Ask the guard to reconsider","Look for another entrance"]}'

    result = ensure_choices(
        db,
        record.nonce,
        session,
        {"name": "Alice"},
        provider_port=ProviderPort(generate),
        app_settings=settings,
    )

    assert result.generation_status == "ready"
    prompt = "\n".join(str(message["content"]) for message in calls[0])
    assert "Offer only plausible actions grounded in the established user persona and situation." in prompt
    assert "Do not assume an action succeeds" in prompt
    assert "wins admiration" in prompt
