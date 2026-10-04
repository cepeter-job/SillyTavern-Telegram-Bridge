"""Transactional migration for the narrative engine's session-owned storage."""

import json
import sqlite3
import time

from bridge.narrative_values import NarrativeSettings

_TABLES = (
    """CREATE TABLE IF NOT EXISTS narrative_defaults (
        owner_user_id TEXT PRIMARY KEY CHECK(length(owner_user_id) BETWEEN 1 AND 100),
        settings_json TEXT NOT NULL CHECK(json_valid(settings_json) AND length(settings_json) <= 4096),
        updated_at REAL NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS narrative_settings (
        chat_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        settings_json TEXT NOT NULL CHECK(json_valid(settings_json) AND length(settings_json) <= 4096),
        settings_revision INTEGER NOT NULL DEFAULT 0 CHECK(settings_revision >= 0),
        updated_at REAL NOT NULL,
        PRIMARY KEY(chat_id,session_id),
        FOREIGN KEY(chat_id,session_id) REFERENCES sessions(chat_id,session_id) ON DELETE CASCADE
    )""",
    """CREATE TABLE IF NOT EXISTS narrative_state (
        chat_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        active_scene_id TEXT NOT NULL DEFAULT '',
        active_thread_id TEXT NOT NULL DEFAULT '',
        story_phase TEXT NOT NULL DEFAULT 'setup'
            CHECK(story_phase IN ('setup','development','escalation','climax','resolution','epilogue','closed')),
        state_revision INTEGER NOT NULL DEFAULT 0 CHECK(state_revision >= 0),
        history_revision INTEGER NOT NULL DEFAULT 0 CHECK(history_revision >= 0),
        updated_through_rowid INTEGER NOT NULL DEFAULT 0 CHECK(updated_through_rowid >= 0),
        updated_at REAL NOT NULL DEFAULT 0,
        PRIMARY KEY(chat_id,session_id),
        FOREIGN KEY(chat_id,session_id) REFERENCES sessions(chat_id,session_id) ON DELETE CASCADE
    )""",
    """CREATE TABLE IF NOT EXISTS narrative_threads (
        chat_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        thread_id TEXT NOT NULL CHECK(length(thread_id) BETWEEN 1 AND 100),
        title TEXT NOT NULL CHECK(length(title) <= 200),
        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','offscreen','dormant','resolved')),
        summary TEXT NOT NULL DEFAULT '' CHECK(length(summary) <= 2000),
        last_scene_id TEXT NOT NULL DEFAULT '',
        source_revision INTEGER NOT NULL DEFAULT 0 CHECK(source_revision >= 0),
        PRIMARY KEY(chat_id,session_id,thread_id),
        FOREIGN KEY(chat_id,session_id) REFERENCES sessions(chat_id,session_id) ON DELETE CASCADE
    )""",
    """CREATE TABLE IF NOT EXISTS narrative_scenes (
        chat_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        scene_id TEXT NOT NULL CHECK(length(scene_id) BETWEEN 1 AND 100),
        thread_id TEXT NOT NULL,
        viewpoint_character TEXT NOT NULL DEFAULT '' CHECK(length(viewpoint_character) <= 200),
        pov_mode TEXT NOT NULL DEFAULT 'third_person_rotating'
            CHECK(pov_mode IN ('first_person','third_person_user','third_person_rotating','omniscient','cinematic')),
        user_present INTEGER CHECK(user_present IN (0,1)),
        purpose TEXT NOT NULL DEFAULT '' CHECK(length(purpose) <= 1000),
        transition_type TEXT NOT NULL DEFAULT 'continue'
            CHECK(transition_type IN ('continue','cut','pov_switch','time_jump','thread_switch')),
        start_rowid INTEGER NOT NULL DEFAULT 0 CHECK(start_rowid >= 0),
        end_rowid INTEGER CHECK(end_rowid >= start_rowid),
        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','complete')),
        source_revision INTEGER NOT NULL DEFAULT 0 CHECK(source_revision >= 0),
        PRIMARY KEY(chat_id,session_id,scene_id),
        FOREIGN KEY(chat_id,session_id) REFERENCES sessions(chat_id,session_id) ON DELETE CASCADE,
        FOREIGN KEY(chat_id,session_id,thread_id)
            REFERENCES narrative_threads(chat_id,session_id,thread_id) DEFERRABLE INITIALLY DEFERRED
    )""",
    """CREATE TABLE IF NOT EXISTS narrative_arcs (
        chat_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        arc_id TEXT NOT NULL CHECK(length(arc_id) BETWEEN 1 AND 100),
        title TEXT NOT NULL CHECK(length(title) <= 200),
        status TEXT NOT NULL DEFAULT 'planned' CHECK(status IN ('planned','active','dormant','resolved','abandoned')),
        phase TEXT NOT NULL DEFAULT 'development',
        importance TEXT NOT NULL DEFAULT 'minor' CHECK(importance IN ('major','minor')),
        summary TEXT NOT NULL DEFAULT '' CHECK(length(summary) <= 2000),
        open_questions_json TEXT NOT NULL DEFAULT '[]'
            CHECK(json_valid(open_questions_json) AND length(open_questions_json) <= 4000),
        related_threads_json TEXT NOT NULL DEFAULT '[]'
            CHECK(json_valid(related_threads_json) AND length(related_threads_json) <= 4000),
        source_revision INTEGER NOT NULL DEFAULT 0 CHECK(source_revision >= 0),
        PRIMARY KEY(chat_id,session_id,arc_id),
        FOREIGN KEY(chat_id,session_id) REFERENCES sessions(chat_id,session_id) ON DELETE CASCADE
    )""",
    """CREATE TABLE IF NOT EXISTS director_state (
        chat_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        goal TEXT NOT NULL DEFAULT '',
        active_direction TEXT NOT NULL DEFAULT '' CHECK(length(active_direction) <= 4000),
        direction_scope TEXT NOT NULL DEFAULT 'next_scene' CHECK(direction_scope IN ('next_scene','persistent')),
        state_revision INTEGER NOT NULL DEFAULT 0 CHECK(state_revision >= 0),
        accepted_through_rowid INTEGER NOT NULL DEFAULT 0 CHECK(accepted_through_rowid >= 0),
        last_director_turn INTEGER NOT NULL DEFAULT 0 CHECK(last_director_turn >= 0),
        last_director_run REAL NOT NULL DEFAULT 0,
        degraded_state TEXT NOT NULL DEFAULT '',
        updated_at REAL NOT NULL DEFAULT 0,
        PRIMARY KEY(chat_id,session_id),
        FOREIGN KEY(chat_id,session_id) REFERENCES sessions(chat_id,session_id) ON DELETE CASCADE
    )""",
    """CREATE TABLE IF NOT EXISTS director_decisions (
        decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        source TEXT NOT NULL CHECK(source IN ('ai','user')),
        result TEXT NOT NULL CHECK(result IN ('accepted','rejected','superseded')),
        expected_revision INTEGER NOT NULL CHECK(expected_revision >= 0),
        proposal_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(proposal_json) AND length(proposal_json) <= 16384),
        accepted_direction TEXT NOT NULL DEFAULT '' CHECK(length(accepted_direction) <= 4000),
        reason TEXT NOT NULL DEFAULT '' CHECK(length(reason) <= 1000),
        created_at REAL NOT NULL,
        FOREIGN KEY(chat_id,session_id) REFERENCES sessions(chat_id,session_id) ON DELETE CASCADE
    )""",
    """CREATE TABLE IF NOT EXISTS narrative_checkpoints (
        checkpoint_id TEXT PRIMARY KEY CHECK(length(checkpoint_id) BETWEEN 1 AND 100),
        chat_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('pre_finale','reconciliation')),
        source_revision INTEGER NOT NULL CHECK(source_revision >= 0),
        through_rowid INTEGER NOT NULL CHECK(through_rowid >= 0),
        format_version INTEGER NOT NULL CHECK(format_version = 1),
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json) AND length(payload_json) <= 1048576),
        created_at REAL NOT NULL,
        UNIQUE(chat_id,session_id,kind,source_revision),
        FOREIGN KEY(chat_id,session_id) REFERENCES sessions(chat_id,session_id) ON DELETE CASCADE
    )""",
    """CREATE TABLE IF NOT EXISTS ending_state (
        chat_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        lifecycle TEXT NOT NULL DEFAULT 'open' CHECK(lifecycle IN
            ('open','finale_ready','finale','resolution_committed','epilogue_pending','epilogue_committed','closed')),
        lifecycle_revision INTEGER NOT NULL DEFAULT 0 CHECK(lifecycle_revision >= 0),
        current_goal TEXT NOT NULL DEFAULT '' CHECK(length(current_goal) <= 4000),
        goal_revision INTEGER NOT NULL DEFAULT 0 CHECK(goal_revision >= 0),
        finale_ready_revision INTEGER,
        finale_direction_revision INTEGER,
        checkpoint_id TEXT,
        finale_operation_id TEXT,
        finale_committed_rowid INTEGER,
        resolution_rowid INTEGER,
        epilogue_operation_id TEXT,
        epilogue_committed_rowid INTEGER,
        updated_at REAL NOT NULL DEFAULT 0,
        PRIMARY KEY(chat_id,session_id),
        FOREIGN KEY(chat_id,session_id) REFERENCES sessions(chat_id,session_id) ON DELETE CASCADE
    )""",
    """CREATE TABLE IF NOT EXISTS ending_goal_history (
        chat_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        goal_revision INTEGER NOT NULL CHECK(goal_revision >= 0),
        source TEXT NOT NULL CHECK(source IN ('director','user')),
        story_revision INTEGER NOT NULL CHECK(story_revision >= 0),
        scene_id TEXT NOT NULL DEFAULT '',
        previous_goal TEXT NOT NULL CHECK(length(previous_goal) <= 4000),
        new_goal TEXT NOT NULL CHECK(length(new_goal) <= 4000),
        reason TEXT NOT NULL CHECK(length(reason) <= 1000),
        created_at REAL NOT NULL,
        PRIMARY KEY(chat_id,session_id,goal_revision),
        FOREIGN KEY(chat_id,session_id) REFERENCES sessions(chat_id,session_id) ON DELETE CASCADE
    )""",
)

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS narrative_scenes_source_idx ON narrative_scenes(chat_id,session_id,source_revision)",
    "CREATE INDEX IF NOT EXISTS narrative_threads_status_idx ON narrative_threads(chat_id,session_id,status)",
    "CREATE INDEX IF NOT EXISTS narrative_arcs_status_idx ON narrative_arcs(chat_id,session_id,status)",
    "CREATE INDEX IF NOT EXISTS director_decisions_session_idx ON director_decisions(chat_id,session_id,decision_id)",
    "CREATE INDEX IF NOT EXISTS ending_state_lifecycle_idx ON ending_state(lifecycle,chat_id,session_id)",
)

_HISTORY_LIMITS = (
    """CREATE TRIGGER IF NOT EXISTS director_decisions_retention AFTER INSERT ON director_decisions BEGIN
        DELETE FROM director_decisions WHERE chat_id=NEW.chat_id AND session_id=NEW.session_id
          AND decision_id IN (SELECT decision_id FROM director_decisions
            WHERE chat_id=NEW.chat_id AND session_id=NEW.session_id ORDER BY decision_id DESC LIMIT -1 OFFSET 200);
    END""",
    """CREATE TRIGGER IF NOT EXISTS ending_goal_history_retention AFTER INSERT ON ending_goal_history BEGIN
        DELETE FROM ending_goal_history WHERE chat_id=NEW.chat_id AND session_id=NEW.session_id
          AND goal_revision IN (SELECT goal_revision FROM ending_goal_history
            WHERE chat_id=NEW.chat_id AND session_id=NEW.session_id ORDER BY goal_revision DESC LIMIT -1 OFFSET 100);
    END""",
)


def verify_director_goal_copy(db: sqlite3.Connection) -> None:
    """Verify identities and exact text before retiring the previous store."""
    source_count = db.execute("SELECT COUNT(*) FROM director_goals").fetchone()[0]
    matched_count = db.execute(
        "SELECT COUNT(*) FROM director_goals g JOIN director_state d "
        "ON d.chat_id=g.chat_id AND d.session_id=g.session_id AND d.goal=g.goal"
    ).fetchone()[0]
    mismatch = db.execute(
        "SELECT 1 FROM director_goals g LEFT JOIN director_state d "
        "ON d.chat_id=g.chat_id AND d.session_id=g.session_id "
        "WHERE d.session_id IS NULL OR d.goal IS NOT g.goal LIMIT 1"
    ).fetchone()
    if source_count != matched_count or mismatch is not None:
        raise ValueError("Director goal copy verification failed")


def migrate_narrative_engine_foundation(db: sqlite3.Connection) -> None:
    """Join the migration runner's transaction; never commit or call providers."""
    if not db.in_transaction:
        raise RuntimeError("Narrative migration requires an active transaction")
    for statement in (*_TABLES, *_INDEXES, *_HISTORY_LIMITS):
        db.execute(statement)
    settings = json.dumps(NarrativeSettings().to_dict(), separators=(",", ":"), sort_keys=True)
    db.execute(
        "INSERT INTO narrative_settings(chat_id,session_id,settings_json,updated_at) "
        "SELECT chat_id,session_id,?,? FROM sessions WHERE true ON CONFLICT(chat_id,session_id) DO NOTHING",
        (settings, time.time()),
    )
    legacy_exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='director_goals'").fetchone()
    if legacy_exists:
        db.execute(
            "INSERT INTO director_state(chat_id,session_id,goal,updated_at) "
            "SELECT chat_id,session_id,goal,updated_at FROM director_goals WHERE true "
            "ON CONFLICT(chat_id,session_id) DO NOTHING"
        )
        verify_director_goal_copy(db)
        db.execute("DROP TRIGGER IF EXISTS director_goals_session_delete")
        db.execute("DROP TABLE director_goals")
