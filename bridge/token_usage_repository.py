"""SQL-only private usage ledger. All writes belong to the caller's transaction."""

from __future__ import annotations

import sqlite3

from bridge.repository_contracts import require_active_transaction


def insert_event(
    db: sqlite3.Connection,
    *,
    chat_id: str,
    session_id: str,
    model: str,
    purpose: str,
    created_at: float,
    status: str,
    elapsed_ms: int,
    input_tokens: int | None,
    output_tokens: int | None,
    total_tokens: int | None,
    cached_tokens: int | None,
    reasoning_tokens: int | None,
    reported: bool,
    complete: bool,
) -> None:
    require_active_transaction(db)
    # A deleted session must not be recreated by an in-flight provider result.
    db.execute(
        """INSERT INTO token_usage_events
        (chat_id,session_id,model,purpose,created_at,status,elapsed_ms,input_tokens,output_tokens,
         total_tokens,cached_tokens,reasoning_tokens,reported,complete)
        SELECT ?,?,?,?,?,?,?,?,?,?,?,?,?,? WHERE EXISTS
        (SELECT 1 FROM sessions WHERE chat_id=? AND session_id=?)""",
        (
            chat_id,
            session_id,
            model,
            purpose,
            created_at,
            status,
            elapsed_ms,
            input_tokens,
            output_tokens,
            total_tokens,
            cached_tokens,
            reasoning_tokens,
            int(reported),
            int(complete),
            chat_id,
            session_id,
        ),
    )


def prune_events(db: sqlite3.Connection, cutoff: float) -> None:
    require_active_transaction(db)
    db.execute("DELETE FROM token_usage_events WHERE created_at<?", (cutoff,))


def usage_totals(db: sqlite3.Connection, chat_id: str, session_id: str | None, since: float, until: float) -> dict:
    params = (chat_id, since, until, session_id, session_id)

    def rows(query: str) -> list[dict]:
        cursor = db.execute(query, params)
        names = [column[0] for column in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]

    totals = rows("""SELECT count(*) AS calls, coalesce(sum(reported),0) AS reported_calls,
    coalesce(sum(complete),0) AS complete_calls,
    coalesce(sum(status='failed'),0) AS failed_calls,
    coalesce(sum(status='cancelled'),0) AS cancelled_calls,
    sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens,
    sum(total_tokens) AS total_tokens, sum(cached_tokens) AS cached_tokens,
    sum(reasoning_tokens) AS reasoning_tokens
    FROM token_usage_events WHERE chat_id=? AND created_at>=? AND created_at<=?
    AND (? IS NULL OR session_id=?) """)[0]
    daily = rows("""SELECT strftime('%Y-%m-%d',created_at,'unixepoch') AS day,
        count(*) AS calls, coalesce(sum(reported),0) AS reported_calls,
    coalesce(sum(complete),0) AS complete_calls,
    coalesce(sum(status='failed'),0) AS failed_calls,
    coalesce(sum(status='cancelled'),0) AS cancelled_calls,
    sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens,
    sum(total_tokens) AS total_tokens, sum(cached_tokens) AS cached_tokens,
    sum(reasoning_tokens) AS reasoning_tokens
    FROM token_usage_events WHERE chat_id=? AND created_at>=? AND created_at<=?
    AND (? IS NULL OR session_id=?) GROUP BY day ORDER BY day LIMIT 31""")
    models = rows("""SELECT model,count(*) AS calls, coalesce(sum(reported),0) AS reported_calls,
    coalesce(sum(complete),0) AS complete_calls,
    coalesce(sum(status='failed'),0) AS failed_calls,
    coalesce(sum(status='cancelled'),0) AS cancelled_calls,
    sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens,
    sum(total_tokens) AS total_tokens, sum(cached_tokens) AS cached_tokens,
    sum(reasoning_tokens) AS reasoning_tokens
    FROM token_usage_events WHERE chat_id=? AND created_at>=? AND created_at<=?
    AND (? IS NULL OR session_id=?) GROUP BY model ORDER BY total_tokens DESC,model LIMIT 12""")
    purposes = rows("""SELECT purpose,count(*) AS calls, coalesce(sum(reported),0) AS reported_calls,
    coalesce(sum(complete),0) AS complete_calls,
    coalesce(sum(status='failed'),0) AS failed_calls,
    coalesce(sum(status='cancelled'),0) AS cancelled_calls,
    sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens,
    sum(total_tokens) AS total_tokens, sum(cached_tokens) AS cached_tokens,
    sum(reasoning_tokens) AS reasoning_tokens
    FROM token_usage_events WHERE chat_id=? AND created_at>=? AND created_at<=?
    AND (? IS NULL OR session_id=?) GROUP BY purpose ORDER BY total_tokens DESC,purpose LIMIT 12""")
    return {"totals": totals, "daily": daily, "models": models, "purposes": purposes}
