"""Canonical response delivery owner."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from collections.abc import Callable
from functools import partial as _partial
from typing import NamedTuple

from bridge.background import submit_background
from bridge.closed_session_guard import story_mutation_message
from bridge.delivery_progress import DeliveryFailure, DeliveryTargetExpired, checkpoint, prepare_progress
from bridge.delivery_repository import clear_progress
from bridge.diagnostic_events import event
from bridge.diagnostic_operations import observe_boundary
from bridge.expressions import deliver_expression
from bridge.metadata import get_meta
from bridge.settings import AppSettings
from bridge.speech import send_tts
from bridge.sqlite_store import write_transaction
from bridge.telegram import send_text, split_telegram_text, telegram_request
from bridge.telegram_output import (
    TelegramFormatSpan,
    telegram_format_spans,
    telegram_safe_output,
    telegram_transport_output,
)
from bridge.text_units import is_escaped_at, utf16_length

_ROLEPLAY_ITALIC = re.compile(r"(?<!\*)\*(?![\s*])(?P<body>.*?)(?<![\s*])\*(?!\*)", re.DOTALL)


def _quoted_speech_ranges(text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    opened_at: int | None = None
    closing = ""
    pairs = {'"': '"', "“": "”"}
    for index, char in enumerate(text):
        if opened_at is None:
            if char in pairs and (char != '"' or not is_escaped_at(text, index)):
                opened_at = index
                closing = pairs[char]
            continue
        if char == closing and (char != '"' or not is_escaped_at(text, index)):
            ranges.append((opened_at, index + 1))
            opened_at = None
            closing = ""
    if opened_at is not None:
        ranges.append((opened_at, len(text)))
    return ranges


def _unquoted_segments(start: int, end: int, quoted: list[tuple[int, int]]) -> list[tuple[int, int]]:
    segments: list[tuple[int, int]] = []
    cursor = start
    for quote_start, quote_end in quoted:
        if quote_end <= cursor:
            continue
        if quote_start >= end:
            break
        if quote_start > cursor:
            segments.append((cursor, min(quote_start, end)))
        cursor = max(cursor, quote_end)
        if cursor >= end:
            break
    if cursor < end:
        segments.append((cursor, end))
    return segments


def _format_ranges(
    spans: list[TelegramFormatSpan],
    entity_types: set[str],
) -> list[tuple[int, int]]:
    return [(span.start, span.end) for span in spans if span.type in entity_types]


def _range_overlaps(start: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start < range_end and range_start < end for range_start, range_end in ranges)


def _roleplay_reply_chunks(text: str) -> list[tuple[str, list[dict[str, int | str]]]]:
    """Render safe Telegram entities plus narration italics on visible text."""
    source, format_spans = telegram_format_spans(text)
    literal_star_ranges = _format_ranges(format_spans, {"code", "pre"})
    matches = [
        match
        for match in _ROLEPLAY_ITALIC.finditer(source)
        if not _range_overlaps(match.start(), match.end(), literal_star_ranges)
    ]
    marker_positions = {position for match in matches for position in (match.start(), match.end() - 1)}
    source_to_visible_utf16 = [0] * (len(source) + 1)
    visible_chars: list[str] = []
    visible_utf16 = 0

    for index, char in enumerate(source):
        source_to_visible_utf16[index] = visible_utf16
        if index not in marker_positions:
            visible_chars.append(char)
            visible_utf16 += utf16_length(char)
        source_to_visible_utf16[index + 1] = visible_utf16

    entities: list[dict[str, int | str]] = []
    for span in format_spans:
        entity_start = source_to_visible_utf16[span.start]
        entity_end = source_to_visible_utf16[span.end]
        if entity_end <= entity_start:
            continue
        entity: dict[str, int | str] = {
            "type": span.type,
            "offset": entity_start,
            "length": entity_end - entity_start,
        }
        if span.url:
            entity["url"] = span.url
        entities.append(entity)

    quoted = _quoted_speech_ranges(source)
    italic_exclusions = sorted(
        [
            *quoted,
            *_format_ranges(format_spans, {"code", "pre", "blockquote", "expandable_blockquote"}),
        ]
    )
    for match in matches:
        for segment_start, segment_end in _unquoted_segments(
            match.start("body"),
            match.end("body"),
            italic_exclusions,
        ):
            entity_start = source_to_visible_utf16[segment_start]
            entity_end = source_to_visible_utf16[segment_end]
            if entity_end > entity_start:
                entities.append(
                    {
                        "type": "italic",
                        "offset": entity_start,
                        "length": entity_end - entity_start,
                    }
                )

    deduped: dict[tuple[str, int, int, str], dict[str, int | str]] = {}
    for entity in entities:
        key = (
            str(entity["type"]),
            int(entity["offset"]),
            int(entity["length"]),
            str(entity.get("url") or ""),
        )
        deduped[key] = entity
    entities = sorted(
        deduped.values(),
        key=lambda entity: (
            int(entity["offset"]),
            -int(entity["length"]),
            str(entity["type"]),
            str(entity.get("url") or ""),
        ),
    )

    visible = "".join(visible_chars)
    rendered: list[tuple[str, list[dict[str, int | str]]]] = []
    utf16_start = 0
    for chunk_text in split_telegram_text(visible):
        utf16_end = utf16_start + utf16_length(chunk_text)
        chunk_entities: list[dict[str, int | str]] = []
        for entity in entities:
            entity_start = int(entity["offset"])
            entity_end = entity_start + int(entity["length"])
            overlap_start = max(entity_start, utf16_start)
            overlap_end = min(entity_end, utf16_end)
            if overlap_start >= overlap_end:
                continue
            chunk_entity: dict[str, int | str] = {
                "type": str(entity["type"]),
                "offset": overlap_start - utf16_start,
                "length": overlap_end - overlap_start,
            }
            if entity.get("url"):
                chunk_entity["url"] = str(entity["url"])
            chunk_entities.append(chunk_entity)
        if chunk_text:
            rendered.append((chunk_text, chunk_entities))
        utf16_start = utf16_end
    return rendered


def _send_reply_chunk(
    token: str,
    chat_id: str,
    chunk: tuple[str, list[dict[str, int | str]]],
    acknowledged: Callable[[int], None] | None = None,
) -> list[int]:
    text, entities = chunk
    if acknowledged is not None and entities:
        return send_text(token, chat_id, text, acknowledged_chunk=acknowledged, entities=entities)
    if acknowledged is not None:
        return send_text(token, chat_id, text, acknowledged_chunk=acknowledged)
    if entities:
        return send_text(token, chat_id, text, entities=entities)
    return send_text(token, chat_id, text)


def collect_session_telegram_ids(db: sqlite3.Connection, chat_id: str, session_id: str) -> list[int]:
    """Freeze tracked transcript IDs before an atomic local reset removes their rows."""
    found = set()
    for role, incoming, outgoing in db.execute(
        "SELECT role,telegram_message_id,telegram_message_ids FROM messages WHERE chat_id=? AND session_id=?",
        (chat_id, session_id),
    ):
        try:
            values = [incoming] if role == "user" else json.loads(outgoing or "[]") if role == "assistant" else []
        except (ValueError, TypeError):
            continue
        if not isinstance(values, list):
            continue
        for value in values:
            try:
                message_id = int(value)
                if message_id > 0:
                    found.add(message_id)
            except (ValueError, TypeError):
                continue
    return sorted(found)


def collect_outgoing_telegram_ids(
    db: sqlite3.Connection, chat_id: str, session_id: str, after_rowid: int | None = None
) -> list[int]:
    query = "SELECT telegram_message_ids FROM messages WHERE chat_id=? AND session_id=? AND role='assistant'"
    params: list[str | int] = [chat_id, session_id]
    if after_rowid is not None:
        query += " AND rowid>?"
        params.append(after_rowid)
    found: set[int] = set()
    for (raw_ids,) in db.execute(query, params).fetchall():
        try:
            message_ids = json.loads(raw_ids or "[]")
        except (TypeError, ValueError):
            continue
        if not isinstance(message_ids, list):
            continue
        for message_id in message_ids:
            try:
                if int(message_id) > 0:
                    found.add(int(message_id))
            except (TypeError, ValueError):
                continue
    return sorted(found)


def delete_outgoing_messages(
    db: sqlite3.Connection, token: str, chat_id: str, session_id: str, after_rowid: int | None = None
) -> None:
    message_ids = collect_outgoing_telegram_ids(db, chat_id, session_id, after_rowid)
    delete_tracked_panel_messages(token, chat_id, message_ids, request=telegram_request)


def delete_incoming_messages(db: sqlite3.Connection, token: str, chat_id: str, session_id: str) -> None:
    rows = db.execute(
        "SELECT telegram_message_id FROM messages "
        "WHERE chat_id=? AND session_id=? AND role='user' AND telegram_message_id IS NOT NULL",
        (chat_id, session_id),
    ).fetchall()
    for (raw_id,) in rows:
        try:
            message_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if message_id <= 0:
            continue
        try:
            telegram_request(token, "deleteMessage", {"chat_id": chat_id, "message_id": message_id})
        except Exception:
            logging.info("Could not delete incoming Telegram message %s", message_id, exc_info=True)


def delete_outgoing_message_row(db: sqlite3.Connection, token: str, chat_id: str, rowid: int) -> None:
    row = db.execute(
        "SELECT telegram_message_ids FROM messages WHERE rowid=? AND chat_id=? AND role='assistant'", (rowid, chat_id)
    ).fetchone()
    if not row:
        return
    try:
        message_ids = json.loads(row[0] or "[]")
    except json.JSONDecodeError:
        message_ids = []
    for message_id in message_ids:
        try:
            telegram_request(token, "deleteMessage", {"chat_id": chat_id, "message_id": int(message_id)})
        except Exception:
            logging.info("Could not delete outgoing Telegram message %s", message_id, exc_info=True)
    with write_transaction(db):
        clear_progress(db, rowid)


def quoted_speech_from_reply(text: str) -> str:
    """Return only dialogue enclosed in straight double quotes for TTS."""
    quoted = re.findall(r'"([^"\n]{1,4000})"', str(text or ""), flags=re.DOTALL)
    return re.sub(r"\s+", " ", " ".join(quoted)).strip()


def queue_user_quote_tts(
    token: str,
    chat_id: str,
    text: str,
    db: sqlite3.Connection,
    session_id: str,
    message_id: int | None = None,
    *,
    app_settings: AppSettings,
) -> bool:
    """Queue quoted user dialogue for TTS without changing the transcript."""
    if story_mutation_message(db, chat_id, session_id):
        return False
    if get_meta(db, f"voice_mode:{chat_id}", "off") != "tts":
        return False
    speech = quoted_speech_from_reply(text)
    if not speech:
        return False
    stable_id = (
        str(message_id)
        if message_id is not None
        else hashlib.sha256(f"{session_id}\0{text}".encode("utf-8")).hexdigest()[:24]
    )
    operation_id = f"user-tts:{chat_id}:{session_id}:{stable_id}"
    if not submit_background(
        "tts", _partial(send_tts, app_settings=app_settings), token, chat_id, speech, operation_id
    ):
        logging.warning("Automatic user-quote TTS dropped for chat %s", chat_id)
        return False
    return True


@observe_boundary("delivery.reply")
def send_reply(
    token: str,
    chat_id: str,
    text: str,
    db: sqlite3.Connection | None = None,
    session_id: str | None = None,
    assistant_rowid: int | None = None,
    *,
    replace_message_id: int | None = None,
    expected_job_id: int | str | None = None,
    app_settings: AppSettings,
) -> None:
    if db is not None and session_id and story_mutation_message(db, chat_id, session_id):
        # Recovery may deliver committed text, but never generate fresh media for a closed original.
        session_id = None
    transport_text = telegram_transport_output(text)
    progress_payload = telegram_safe_output(transport_text)
    message_ids: list[int] = []
    complete = False
    expected_source = None
    if db is not None and assistant_rowid is not None:
        try:
            progress_payload, message_ids, complete, expected_source = prepare_progress(
                db,
                assistant_rowid,
                progress_payload,
                expected_job_id=expected_job_id,
            )
        except DeliveryTargetExpired:
            raise
        except Exception as exc:
            raise DeliveryFailure("Reply committed; delivery checkpoint could not be prepared") from exc
        if complete:
            event("delivery.already_complete", source_message_id=assistant_rowid)
            return
    chunks = _roleplay_reply_chunks(transport_text)
    visible_text = "".join(chunk_text for chunk_text, _ in chunks)

    def acknowledged(message_id: int) -> None:
        event("delivery.chunk_acknowledged", message_id=message_id, source_message_id=assistant_rowid)
        message_ids.append(message_id)
        if db is not None and assistant_rowid is not None:
            checkpoint(
                db,
                assistant_rowid,
                message_ids,
                expected_job_id=expected_job_id,
                expected_source=expected_source,
                expected_payload=progress_payload,
            )

    try:
        if db is not None and session_id and not message_ids:
            deliver_expression(token, chat_id, visible_text, db, session_id, app_settings=app_settings)
        if db is None or assistant_rowid is None:
            # Preserve the public adapter's ordinary untracked send behavior.
            if replace_message_id is None:
                for chunk in chunks:
                    _send_reply_chunk(token, chat_id, chunk)
            else:
                _send_preview(token, chat_id, chunks, replace_message_id, acknowledged)
        else:
            if not message_ids and replace_message_id is not None:
                _send_preview(token, chat_id, chunks[:1], replace_message_id, acknowledged)
            remaining = chunks[len(message_ids) :]
            for chunk in remaining:
                _send_reply_chunk(token, chat_id, chunk, acknowledged)
            if len(message_ids) != len(chunks):
                raise DeliveryFailure("Telegram did not acknowledge every reply chunk")
            checkpoint(
                db,
                assistant_rowid,
                message_ids,
                complete=True,
                expected_job_id=expected_job_id,
                expected_source=expected_source,
                expected_payload=progress_payload,
            )
    except DeliveryFailure:
        event("delivery.incomplete", status="failed", source_message_id=assistant_rowid)
        raise
    except Exception as exc:
        if db is None or assistant_rowid is None:
            raise
        raise DeliveryFailure("Reply committed; Telegram delivery is incomplete") from exc
    if db is not None and session_id and get_meta(db, f"voice_mode:{chat_id}", "off") == "tts":
        speech = quoted_speech_from_reply(visible_text)
        if speech:
            operation_id = None
            if assistant_rowid is not None:
                speech_hash = hashlib.sha256(speech.encode("utf-8")).hexdigest()[:16]
                operation_id = f"assistant-tts:{chat_id}:{session_id}:{assistant_rowid}:{speech_hash}"
            if not submit_background(
                "tts", _partial(send_tts, app_settings=app_settings), token, chat_id, speech, operation_id
            ):
                logging.warning("Automatic TTS dropped for chat %s", chat_id)


def _send_preview(
    token: str,
    chat_id: str,
    chunks: list[tuple[str, list[dict[str, int | str]]]],
    preview_id: int,
    acknowledged: Callable[[int], None],
) -> None:
    text, entities = chunks[0]
    payload: dict[str, object] = {
        "chat_id": chat_id,
        "message_id": preview_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if entities:
        payload["entities"] = entities
    try:
        telegram_request(token, "editMessageText", payload)
    except RuntimeError as exc:
        detail = str(exc).casefold()
        if "message to edit not found" in detail:
            _send_reply_chunk(token, chat_id, chunks[0], acknowledged)
        elif "message is not modified" in detail:
            acknowledged(preview_id)
        else:
            raise
    else:
        acknowledged(preview_id)
    for chunk in chunks[1:]:
        _send_reply_chunk(token, chat_id, chunk, acknowledged)


class TelegramDeletionReport(NamedTuple):
    """Counts for best-effort Telegram cleanup after local changes are committed."""

    attempted: int
    deleted: int
    failed: int


def delete_tracked_panel_messages(
    token: str, chat_id: str, message_ids: list[int], *, request: Callable
) -> TelegramDeletionReport:
    """Attempt each tracked message once; report failures without undoing local changes."""
    deleted = 0
    failures: set[str] = set()
    for message_id in message_ids:
        try:
            request(token, "deleteMessage", {"chat_id": chat_id, "message_id": message_id})
            deleted += 1
        except Exception as exc:
            # Exception classes are safe to log; do not log chat IDs, tokens or API error bodies.
            failures.add(type(exc).__name__)
    failed = len(message_ids) - deleted
    if failed:
        logging.warning(
            "Telegram message cleanup incomplete: deleted=%d attempted=%d failed=%d error_types=%s",
            deleted,
            len(message_ids),
            failed,
            ",".join(sorted(failures)),
        )
    return TelegramDeletionReport(attempted=len(message_ids), deleted=deleted, failed=failed)
