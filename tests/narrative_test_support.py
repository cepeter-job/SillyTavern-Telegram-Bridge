"""Representative committed narrative state shared by lifecycle regression tests."""

from bridge.narrative_settings import (
    preset_narrative_settings,
    save_session_narrative_settings,
    save_user_narrative_default,
)
from bridge.sqlite_store import write_transaction

NARRATIVE_DERIVED_TABLES = (
    "narrative_state",
    "narrative_scenes",
    "narrative_threads",
    "narrative_arcs",
    "director_state",
    "director_decisions",
    "ending_state",
    "ending_goal_history",
    "narrative_checkpoints",
)


def seed_narrative_story(db, chat_id, session_id):
    save_session_narrative_settings(db, chat_id, session_id, preset_narrative_settings("observer"))
    save_user_narrative_default(db, "owner", preset_narrative_settings("world_driven"))
    with write_transaction(db):
        rowid = db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            (chat_id, session_id, "assistant", "Mara waits by the gate.", 1),
        ).lastrowid
        db.execute(
            "INSERT INTO narrative_threads(chat_id,session_id,thread_id,title) VALUES(?,?,?,?)",
            (chat_id, session_id, "thread", "The rebellion"),
        )
        db.execute(
            "INSERT INTO narrative_scenes(chat_id,session_id,scene_id,thread_id) VALUES(?,?,?,?)",
            (chat_id, session_id, "scene", "thread"),
        )
        db.execute(
            "UPDATE narrative_state SET active_scene_id='scene',active_thread_id='thread' "
            "WHERE chat_id=? AND session_id=?",
            (chat_id, session_id),
        )
        db.execute(
            "INSERT INTO narrative_arcs(chat_id,session_id,arc_id,title) VALUES(?,?,?,?)",
            (chat_id, session_id, "arc", "Open the gate"),
        )
        db.execute(
            "INSERT INTO director_state(chat_id,session_id,goal) VALUES(?,?,?)", (chat_id, session_id, "Protect Mara")
        )
        db.execute(
            "INSERT INTO director_decisions(chat_id,session_id,source,result,expected_revision,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (chat_id, session_id, "ai", "accepted", 0, 1),
        )
        db.execute(
            "INSERT INTO ending_state(chat_id,session_id,current_goal) VALUES(?,?,?)",
            (chat_id, session_id, "Peace at the gate"),
        )
        db.execute(
            "INSERT INTO ending_goal_history(chat_id,session_id,goal_revision,source,story_revision,"
            "previous_goal,new_goal,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (chat_id, session_id, 1, "user", 1, "", "Peace at the gate", "Initial goal", 1),
        )
        db.execute(
            "INSERT INTO narrative_checkpoints(checkpoint_id,chat_id,session_id,kind,source_revision,"
            "through_rowid,format_version,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            ("fixture-" + session_id, chat_id, session_id, "reconciliation", 1, rowid, 1, "{}", 1),
        )
        db.execute(
            "INSERT INTO session_summaries(chat_id,session_id,summary,covered_until_rowid,updated_at) "
            "VALUES(?,?,?,?,?)",
            (chat_id, session_id, "Mara waits at the gate.", rowid, 1),
        )
