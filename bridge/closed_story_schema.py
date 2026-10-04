"""Final database defenses against stale callbacks, late workers and reopen attempts."""

import sqlite3

_CLOSING = "('resolution_committed','epilogue_pending','epilogue_committed','closed')"


def _ending(scope: str, condition: str) -> str:
    if scope not in {"OLD", "NEW"}:
        raise ValueError("Invalid migration row alias")
    # Both alias and predicate are compile-time migration constants, never user input.
    return (
        "EXISTS(SELECT 1 FROM ending_state e WHERE e.chat_id=" + scope + ".chat_id "  # noqa: S608
        "AND e.session_id=" + scope + ".session_id AND " + condition + ")"
    )


def _parent(scope: str) -> str:
    if scope == "OLD":
        return "EXISTS(SELECT 1 FROM sessions s WHERE s.chat_id=OLD.chat_id AND s.session_id=OLD.session_id)"
    if scope == "NEW":
        return "EXISTS(SELECT 1 FROM sessions s WHERE s.chat_id=NEW.chat_id AND s.session_id=NEW.session_id)"
    raise ValueError("Invalid migration row alias")


def migrate_closed_story_guards(db: sqlite3.Connection) -> None:
    if not db.in_transaction:
        raise RuntimeError("Closed-story migration requires an active transaction")
    db.execute("CREATE INDEX ending_recovery_due_idx ON ending_state(lifecycle,last_attempt_at,updated_at)")
    db.execute(
        "CREATE TRIGGER closing_story_insert BEFORE INSERT ON messages WHEN "
        + _ending("NEW", "e.lifecycle IN " + _CLOSING)
        + " AND NOT (NEW.role='assistant' AND "
        + _ending("NEW", "e.lifecycle='epilogue_pending' AND e.work_stage='commit_epilogue' AND e.work_token<>''")
        + ") BEGIN SELECT RAISE(ABORT,'The closing story is immutable'); END"
    )
    fields: tuple[str, ...] = ("content", "role", "chat_id", "session_id", "created_at")
    changed = " OR ".join(f"NEW.{field} IS NOT OLD.{field}" for field in fields)
    db.execute(
        "CREATE TRIGGER closing_story_edit BEFORE UPDATE OF "
        + ",".join(fields)
        + " ON messages WHEN ("
        + changed
        + ") AND ("
        + _ending("OLD", "e.lifecycle IN " + _CLOSING)
        + " OR "
        + _ending("NEW", "e.lifecycle IN " + _CLOSING)
        + ") BEGIN SELECT RAISE(ABORT,'The closing story is immutable'); END"
    )
    db.execute(
        "CREATE TRIGGER closing_story_delete BEFORE DELETE ON messages WHEN "
        + _ending("OLD", "e.lifecycle IN " + _CLOSING)
        + " BEGIN SELECT RAISE(ABORT,'The closing story is immutable'); END"
    )
    immutable = (
        "lifecycle",
        "current_goal",
        "goal_revision",
        "checkpoint_id",
        "finale_operation_id",
        "finale_committed_rowid",
        "resolution_rowid",
        "epilogue_operation_id",
        "epilogue_committed_rowid",
        "epilogue_brief_json",
        "resolution_evidence_json",
        "required_arcs_json",
    )
    changed = " OR ".join(f"NEW.{field} IS NOT OLD.{field}" for field in immutable)
    db.execute(
        "CREATE TRIGGER closed_ending_update BEFORE UPDATE ON ending_state WHEN OLD.lifecycle='closed' AND ("
        + changed
        + ") BEGIN SELECT RAISE(ABORT,'The completed ending is immutable'); END"
    )
    db.execute(
        "CREATE TRIGGER closing_ending_delete BEFORE DELETE ON ending_state WHEN OLD.lifecycle IN "
        + _CLOSING
        + " AND "
        + _parent("OLD")
        + " BEGIN SELECT RAISE(ABORT,'The completed ending is immutable'); END"
    )
    for table in (
        "narrative_state",
        "narrative_scenes",
        "narrative_threads",
        "narrative_arcs",
        "scene_states",
        "narrative_settings",
        "director_decisions",
        "ending_goal_history",
    ):
        for action in ("INSERT", "UPDATE", "DELETE"):
            row = "NEW" if action == "INSERT" else "OLD"
            db.execute(
                f"CREATE TRIGGER closed_{table}_{action.lower()} BEFORE {action} ON {table} WHEN "
                + _ending(row, "e.lifecycle='closed'")
                + " AND "
                + _parent(row)
                + " BEGIN SELECT RAISE(ABORT,'The closed narrative is immutable'); END"
            )
    # Director leases may be released after closure; its actual plans may not change.
    fields = (
        "goal",
        "active_direction",
        "active_proposal_json",
        "direction_scope",
        "direction_source",
        "arc_guidance_json",
    )
    changed = " OR ".join(f"NEW.{field} IS NOT OLD.{field}" for field in fields)
    db.execute(
        "CREATE TRIGGER closed_director_update BEFORE UPDATE ON director_state WHEN ("
        + changed
        + ") AND "
        + _ending("OLD", "e.lifecycle='closed'")
        + " BEGIN SELECT RAISE(ABORT,'The closed Director plan is immutable'); END"
    )
    for action in ("INSERT", "DELETE"):
        row = "NEW" if action == "INSERT" else "OLD"
        db.execute(
            f"CREATE TRIGGER closed_director_{action.lower()} BEFORE {action} ON director_state WHEN "
            + _ending(row, "e.lifecycle='closed'")
            + " AND "
            + _parent(row)
            + " BEGIN SELECT RAISE(ABORT,'The closed Director plan is immutable'); END"
        )
    fields = (
        "character_file",
        "model_id",
        "persona_id",
        "world_file",
        "author_note",
        "system_prompt",
        "response_language",
    )
    changed = " OR ".join(f"NEW.{field} IS NOT OLD.{field}" for field in fields)
    db.execute(
        "CREATE TRIGGER closed_session_configuration BEFORE UPDATE ON sessions WHEN ("
        + changed
        + ") AND "
        + _ending("OLD", "e.lifecycle='closed'")
        + " BEGIN SELECT RAISE(ABORT,'The completed story configuration is immutable'); END"
    )
