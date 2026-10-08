"""Source-bound, shadow-only historical framing contracts."""

import json

from bridge.context_history_preview import preview_packed_history
from bridge.generation import format_user_dialogue_action
from bridge.memory_contracts import MemoryReadScope


def source(count=38):
    rows = tuple((i + 1, "user" if i % 2 == 0 else "assistant", f"Archive routine {i}: checked.") for i in range(count))
    messages = [{"role": "system", "content": "Fixed instructions."}]
    for index, (_rowid, role, content) in enumerate(rows):
        messages.append(
            {
                "role": role,
                "content": format_user_dialogue_action(content) if role == "user" else content,
                "_context_history_index": index,
            }
        )
    messages.append({"role": "user", "content": "Now inspect the fresh document."})
    return rows, messages


def scope(**changes):
    values = dict(
        chat_id="c",
        session_id="s",
        session_created_at=1,
        through_rowid=100,
        rewrite_revision=2,
        principals=("rowan",),
        consumer="character",
        historical=False,
    )
    values.update(changes)
    return MemoryReadScope(**values)


def reconstruct(messages):
    restored = []
    for message in messages:
        if "_context_history_index" in message:
            restored.append((message["role"], message["content"]))
        elif message.get("_history_preview_packet"):
            data = json.loads(message["content"].split("\n", 1)[1])
            restored.extend((role, content) for role, content in data)
    return restored


def test_preview_preserves_all_source_words_and_role_order_without_modifying_input():
    rows, messages = source()
    original = [dict(message) for message in messages]
    result, count, reason = preview_packed_history(messages, rows, scope(), coverage_valid=True, query="fresh document")
    assert reason == "selected"
    assert count > 0
    assert messages == original
    assert reconstruct(result) == [(role, content) for _, role, content in rows]
    assert result[0] == messages[0]
    assert result[-1] == messages[-1]
    assert [item["content"] for item in result[-9:-1]] == [row[2] for row in rows[-8:]]
    assert all(item.get("role") != "system" for item in result[1:-1])


def test_callback_negation_and_promise_are_not_reframed():
    rows, messages = source()
    for pos, content in (
        (9, "Rowan promised never to open the sealed vault."),
        (18, "The key was not handed to Mira."),
        (24, "Remember the locked door for later."),
    ):
        row = rows[pos]
        rows = (*rows[:pos], (row[0], row[1], content), *rows[pos + 1 :])
        messages[pos + 1]["content"] = content
    result, count, reason = preview_packed_history(messages, rows, scope(), coverage_valid=True, query="fresh document")
    assert reason == "selected" and count > 0
    assert reconstruct(result) == [(role, content) for _, role, content in rows]
    for anchor in ("Rowan promised never", "The key was not", "Remember the locked"):
        assert any(item.get("content", "").startswith(anchor) for item in result if "_context_history_index" in item)


def test_case_sensitive_repeated_turns_are_not_deduplicated():
    rows, messages = source()
    repeated = (12, 14)
    for position in repeated:
        old = rows[position]
        rows = (*rows[:position], (old[0], old[1], "Repeat. Repeat."), *rows[position + 1 :])
        messages[position + 1]["content"] = "Repeat. Repeat."
    result, _, _ = preview_packed_history(messages, rows, scope(), coverage_valid=True, query="fresh document")
    assert reconstruct(result).count(("user", "Repeat. Repeat.")) == 2


def test_unknown_or_rewritten_source_never_changes_the_prompt():
    rows, messages = source()
    for broken in (rows[:-1], (*rows[:-1], (38, "assistant", "Rewritten.")), rows[::-1]):
        result, count, reason = preview_packed_history(messages, broken, scope(), coverage_valid=True, query="fresh")
        assert result == messages and count == 0 and reason == "ambiguous"


def test_invalid_scope_or_incomplete_coverage_fails_closed():
    rows, messages = source()
    for selected_scope, valid in (
        (scope(historical=True), True),
        (scope(through_rowid=9), True),
        (scope(principals=()), True),
        (scope(), False),
    ):
        result, count, reason = preview_packed_history(
            messages, rows, selected_scope, coverage_valid=valid, query="fresh"
        )
        assert result == messages and count == 0
        assert reason in {"historical", "invalid_scope", "incomplete_coverage"}


def test_multilingual_older_history_is_a_conservative_baseline():
    rows, messages = source()
    old = rows[10]
    rows = (*rows[:10], (old[0], old[1], "Jangan membuka pintu ini. 門"), *rows[11:])
    messages[11]["content"] = "Jangan membuka pintu ini. 門"
    result, count, reason = preview_packed_history(messages, rows, scope(), coverage_valid=True, query="fresh")
    assert result == messages and count == 0 and reason == "ambiguous"


def test_insufficient_older_transcript_does_not_reframe():
    rows, messages = source(7)
    result, count, reason = preview_packed_history(messages, rows, scope(), coverage_valid=True, query="fresh")
    assert result == messages and count == 0 and reason == "no_savings"
