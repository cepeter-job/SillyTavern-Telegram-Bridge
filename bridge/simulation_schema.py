"""Canonical session-scoped simulation tracker storage."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time

from bridge.npc_repository import set_npc_extraction_coverage
from bridge.npc_rollback import rollback_from_row as rollback_npc_from_row
from bridge.simulation_repository import rollback_from_row as rollback_simulation_from_row


def migrate_simulation_trackers(db: sqlite3.Connection) -> None:
    if not db.in_transaction:
        raise RuntimeError("Simulation tracker migration requires an active transaction")
    db.execute("""CREATE TABLE simulation_revisions(
        chat_id TEXT NOT NULL,session_id TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 0,
        backfill_through INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(chat_id,session_id),
        FOREIGN KEY(chat_id,session_id) REFERENCES sessions(chat_id,session_id) ON DELETE CASCADE)""")
    db.execute("""CREATE TABLE simulation_sources(
        chat_id TEXT NOT NULL,session_id TEXT NOT NULL,source_rowid INTEGER NOT NULL,
        source_digest TEXT NOT NULL,PRIMARY KEY(chat_id,session_id,source_rowid),
        FOREIGN KEY(chat_id,session_id) REFERENCES sessions(chat_id,session_id) ON DELETE CASCADE)""")
    db.execute(
        """
        CREATE TABLE simulation_state(
            chat_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            entity_key TEXT NOT NULL,
            value_json TEXT NOT NULL CHECK(json_valid(value_json) AND length(value_json)<=16384),
            updated_rowid INTEGER NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY(chat_id,session_id,kind,entity_key),
            FOREIGN KEY(chat_id,session_id) REFERENCES sessions(chat_id,session_id) ON DELETE CASCADE
        )
        """
    )
    db.execute(
        "CREATE INDEX simulation_state_session_kind_idx ON simulation_state(chat_id,session_id,kind,updated_rowid)"
    )
    db.execute(
        """
        CREATE TABLE simulation_state_history(
            change_id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            entity_key TEXT NOT NULL,
            before_json TEXT,
            after_json TEXT,
            before_rowid INTEGER NOT NULL DEFAULT 0,
            after_rowid INTEGER NOT NULL DEFAULT 0,
            source_rowid INTEGER NOT NULL,
            created_at REAL NOT NULL,
            FOREIGN KEY(chat_id,session_id) REFERENCES sessions(chat_id,session_id) ON DELETE CASCADE
        )
        """
    )
    db.execute(
        "CREATE INDEX simulation_history_session_row_idx "
        "ON simulation_state_history(chat_id,session_id,source_rowid,change_id)"
    )
    db.execute(
        """
        CREATE TABLE simulation_checks(
            check_id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            request_key TEXT NOT NULL,
            source_rowid INTEGER NOT NULL,
            domain TEXT NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            dc INTEGER NOT NULL,
            roll INTEGER NOT NULL,
            modifier INTEGER NOT NULL,
            delta INTEGER NOT NULL,
            outcome TEXT NOT NULL,
            created_at REAL NOT NULL,
            source_digest TEXT NOT NULL,
            UNIQUE(chat_id,session_id,request_key),
            FOREIGN KEY(chat_id,session_id) REFERENCES sessions(chat_id,session_id) ON DELETE CASCADE
        )
        """
    )
    db.execute(
        "CREATE INDEX simulation_checks_session_row_idx ON simulation_checks(chat_id,session_id,source_rowid,check_id)"
    )


def retire_prompt_tracker_bootstrap(db: sqlite3.Connection) -> None:
    """Stop the retired replay while retaining already accepted native state."""
    if not db.in_transaction:
        raise RuntimeError("Tracker bootstrap retirement requires an active transaction")
    rows = db.execute(
        "SELECT r.chat_id,r.session_id,s.created_at,r.backfill_through,"
        "COALESCE(n.updated_through_rowid,0),COALESCE(l.covered_id,0),l.invalidated_from_id "
        "FROM simulation_revisions r JOIN sessions s USING(chat_id,session_id) "
        "LEFT JOIN npc_extraction_state n USING(chat_id,session_id) "
        "LEFT JOIN memory_layer_state l ON l.chat_id=r.chat_id AND l.session_id=r.session_id "
        "AND l.session_created_at=s.created_at AND l.layer='npc' WHERE r.backfill_through>0"
    ).fetchall()
    for chat_id, session_id, created, old_through, native_through, covered, invalidated in rows:
        resume = max(old_through, native_through, covered)
        if invalidated is not None:
            resume = min(resume, max(0, invalidated - 1))
        resume = db.execute(
            "SELECT COALESCE(MAX(id),0) FROM messages WHERE chat_id=? AND session_id=? AND id<=?",
            (chat_id, session_id, resume),
        ).fetchone()[0]
        # Retain the native prefix while rolling back any genuinely rewritten
        # suffix before a seeded draft can bypass normal restoration.
        now = time.time()
        if invalidated is not None:
            rollback_npc_from_row(db, chat_id, session_id, invalidated, now=now)
            rollback_simulation_from_row(db, chat_id, session_id, invalidated, now=now)
        set_npc_extraction_coverage(db, chat_id, session_id, resume, now)
        db.execute(
            "UPDATE memory_segments SET valid=0 WHERE chat_id=? AND session_id=? AND session_created_at=? "
            "AND layer='npc' AND end_id>?",
            (chat_id, session_id, created, resume),
        )
        db.execute(
            "INSERT INTO memory_layer_state"
            "(chat_id,session_id,session_created_at,layer,covered_id,rewrite_identity) VALUES(?,?,?,'npc',?,1) "
            "ON CONFLICT(chat_id,session_id,session_created_at,layer) DO UPDATE SET "
            "covered_id=excluded.covered_id,rewrite_identity=memory_layer_state.rewrite_identity+1",
            (chat_id, session_id, created, resume),
        )
        # NPC fields are already durable. Resume their complete native prefix;
        # the next row starts a fresh accumulator, not a legacy replay draft.
        # Write after the rewrite trigger has fenced and cleared the old draft.
        db.execute(
            "UPDATE memory_layer_state SET draft_json=?,draft_source_id='' "
            "WHERE chat_id=? AND session_id=? AND session_created_at=? AND layer='npc'",
            ("{}" if resume else "", chat_id, session_id, created),
        )
        db.execute(
            "UPDATE memory_jobs SET dirty_version=dirty_version+1,lease_token='',lease_deadline=0,"
            "claimed_version=0,claimed_target_id=0,next_attempt_at=0 "
            "WHERE chat_id=? AND session_id=? AND session_created_at=? AND layer='npc'",
            (chat_id, session_id, created),
        )
    db.execute("ALTER TABLE simulation_revisions DROP COLUMN backfill_through")


_RETIRED_TRACKER_WRAPPER = re.compile(r"(?is)<tg-spoiler\b[^>]*>.*?</tg-spoiler\s*>")
_RETIRED_TRACKER_SIGNATURE = re.compile(
    r"(?is)INTERNAL STATES.*?(NPC AGENDAS|NPC LOCATIONS).*?BONDS.*?QUESTS.*?"
    r"INV\s*&\s*SKILLS.*?(GM(?:'S)? NOTEBOOK|DND TASK SIM|PHYSICS)"
)


def _strip_retired_tracker_wrappers(content: str) -> str:
    matches = [
        match
        for match in _RETIRED_TRACKER_WRAPPER.finditer(content)
        if _RETIRED_TRACKER_SIGNATURE.search(match.group(0))
    ]
    if not matches:
        return content
    result = content
    for match in reversed(matches):
        result = result[: match.start()] + result[match.end() :]
    return result.rstrip()


def _transcript_fingerprint(db: sqlite3.Connection, chat_id: str, session_id: str, through_rowid: int) -> dict:
    digest = hashlib.sha256()
    count = 0
    rows = db.execute(
        "SELECT id,role,content,created_at FROM messages WHERE chat_id=? AND session_id=? AND id<=? ORDER BY id",
        (chat_id, session_id, int(through_rowid)),
    )
    try:
        for row in rows:
            digest.update(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode())
            digest.update(b"\n")
            count += 1
    finally:
        rows.close()
    return {"count": count, "sha256": digest.hexdigest()}


def _suspend_update_triggers(db: sqlite3.Connection) -> list[tuple[str, str]]:
    result = []
    for name, sql in db.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name='messages' AND sql IS NOT NULL"
    ):
        head = str(sql).split("BEGIN", 1)[0]
        if re.search(r"\b(?:BEFORE|AFTER)\s+UPDATE\b", head, flags=re.IGNORECASE):
            result.append((str(name), str(sql)))
    checkpoint = db.execute(
        "SELECT name,sql FROM sqlite_master "
        "WHERE type='trigger' AND name='pre_finale_checkpoint_immutable' AND sql IS NOT NULL"
    ).fetchone()
    if checkpoint is not None:
        result.append((str(checkpoint[0]), str(checkpoint[1])))
    for name, _sql in result:
        db.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
    return result


def _restore_triggers(db: sqlite3.Connection, triggers: list[tuple[str, str]]) -> None:
    for _name, sql in triggers:
        db.execute(sql)


def _invalidate_non_npc_memory(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    earliest: int,
) -> None:
    session = db.execute(
        "SELECT created_at FROM sessions WHERE chat_id=? AND session_id=?",
        (chat_id, session_id),
    ).fetchone()
    if session is None:
        return
    created = float(session[0])
    latest = int(
        db.execute(
            "SELECT COALESCE(MAX(id),0) FROM messages WHERE chat_id=? AND session_id=?",
            (chat_id, session_id),
        ).fetchone()[0]
    )
    db.execute(
        "UPDATE memory_segments SET valid=0 WHERE chat_id=? AND session_id=? AND end_id>=?",
        (chat_id, session_id, int(earliest)),
    )
    db.execute(
        "UPDATE memory_layer_state SET rewrite_identity=rewrite_identity+1,"
        "invalidated_from_id=CASE WHEN invalidated_from_id IS NULL THEN ? "
        "ELSE MIN(invalidated_from_id,?) END,"
        "covered_id=CASE WHEN layer IN ('hindsight','episodes','summary','scene') "
        "THEN MAX(source_floor_id,MIN(covered_id,?-1)) ELSE 0 END "
        "WHERE chat_id=? AND session_id=? AND session_created_at=? AND layer<>'npc'",
        (earliest, earliest, earliest, chat_id, session_id, created),
    )
    db.execute(
        "DELETE FROM session_summaries WHERE chat_id=? AND session_id=?",
        (chat_id, session_id),
    )
    db.execute(
        "DELETE FROM episodic_memories WHERE chat_id=? AND session_id=? AND source_end_rowid>=?",
        (chat_id, session_id, earliest),
    )
    db.execute(
        "DELETE FROM scene_states WHERE chat_id=? AND session_id=?",
        (chat_id, session_id),
    )
    db.execute("DELETE FROM meta WHERE key=?", (f"memory_curator:{chat_id}:{session_id}",))
    db.execute(
        "UPDATE memory_jobs SET dirty_version=dirty_version+1,target_id=?,"
        "lease_token='',lease_deadline=0,claimed_version=0,claimed_target_id=0,next_attempt_at=0 "
        "WHERE chat_id=? AND session_id=? AND session_created_at=? AND layer<>'npc'",
        (latest, chat_id, session_id, created),
    )


def migrate_retired_tracker_transcript_cleanup(db: sqlite3.Connection) -> None:
    """Remove retired assistant tracker ledgers without treating prose as rewritten story."""
    if not db.in_transaction:
        raise RuntimeError("Tracker transcript cleanup requires an active transaction")

    changes = []
    affected: dict[tuple[str, str], int] = {}
    rows = db.execute(
        "SELECT id,chat_id,session_id,content FROM messages "
        "WHERE role='assistant' AND lower(content) LIKE '%internal states%' ORDER BY id"
    ).fetchall()
    for row_id, chat_id, session_id, content in rows:
        cleaned = _strip_retired_tracker_wrappers(str(content))
        if cleaned == content:
            continue
        changes.append((int(row_id), str(chat_id), str(session_id), cleaned))
        key = (str(chat_id), str(session_id))
        affected[key] = min(affected.get(key, int(row_id)), int(row_id))
    if not changes:
        return

    triggers = _suspend_update_triggers(db)
    try:
        for row_id, chat_id, session_id, cleaned in changes:
            db.execute("UPDATE messages SET content=? WHERE id=?", (cleaned, row_id))
            digest = hashlib.sha256(("assistant\0" + cleaned).encode()).hexdigest()
            db.execute(
                "UPDATE simulation_sources SET source_digest=? WHERE chat_id=? AND session_id=? AND source_rowid=?",
                (digest, chat_id, session_id, row_id),
            )
            db.execute(
                "UPDATE simulation_checks SET source_digest=? WHERE chat_id=? AND session_id=? AND source_rowid=?",
                (digest, chat_id, session_id, row_id),
            )
        for (chat_id, session_id), earliest in affected.items():
            _invalidate_non_npc_memory(db, chat_id, session_id, earliest)
            checkpoints = db.execute(
                "SELECT checkpoint_id,payload_json,through_rowid FROM narrative_checkpoints "
                "WHERE chat_id=? AND session_id=? AND through_rowid>=?",
                (chat_id, session_id, earliest),
            ).fetchall()
            for checkpoint_id, payload_json, through_rowid in checkpoints:
                payload = json.loads(payload_json)
                if "transcript" not in payload:
                    continue
                payload["transcript"] = _transcript_fingerprint(db, chat_id, session_id, int(through_rowid))
                db.execute(
                    "UPDATE narrative_checkpoints SET payload_json=? WHERE checkpoint_id=?",
                    (json.dumps(payload, ensure_ascii=False, separators=(",", ":")), checkpoint_id),
                )
    finally:
        _restore_triggers(db, triggers)