"""Database guards protect an admitted finale's immutable restoration boundary."""

import sqlite3


def migrate_finale_checkpoint_guards(db: sqlite3.Connection) -> None:
    if not db.in_transaction:
        raise RuntimeError("Finale checkpoint migration requires an active transaction")
    db.execute(
        "CREATE TRIGGER pre_finale_checkpoint_immutable BEFORE UPDATE ON narrative_checkpoints "
        "WHEN OLD.kind='pre_finale' BEGIN SELECT RAISE(ABORT,'The pre-finale checkpoint is immutable'); END"
    )
    db.execute(
        "CREATE TRIGGER pre_finale_checkpoint_delete BEFORE DELETE ON narrative_checkpoints "
        "WHEN OLD.kind='pre_finale' AND EXISTS(SELECT 1 FROM ending_state e "
        "WHERE e.checkpoint_id=OLD.checkpoint_id) AND EXISTS(SELECT 1 FROM sessions s "
        "WHERE s.chat_id=OLD.chat_id AND s.session_id=OLD.session_id) "
        "BEGIN SELECT RAISE(ABORT,'The linked pre-finale checkpoint is immutable'); END"
    )
    db.execute(
        "CREATE TRIGGER pre_finale_prefix_edit BEFORE UPDATE OF content,role,chat_id,session_id,created_at ON messages "
        "WHEN (NEW.content IS NOT OLD.content OR NEW.role IS NOT OLD.role OR NEW.chat_id IS NOT OLD.chat_id "
        "OR NEW.session_id IS NOT OLD.session_id OR NEW.created_at IS NOT OLD.created_at) "
        "AND EXISTS(SELECT 1 FROM ending_state e JOIN narrative_checkpoints c ON c.checkpoint_id=e.checkpoint_id "
        "WHERE e.chat_id=OLD.chat_id AND e.session_id=OLD.session_id AND OLD.id<=c.through_rowid) "
        "BEGIN SELECT RAISE(ABORT,'The pre-finale transcript checkpoint is immutable'); END"
    )
    db.execute(
        "CREATE TRIGGER pre_finale_prefix_delete BEFORE DELETE ON messages "
        "WHEN EXISTS(SELECT 1 FROM ending_state e JOIN narrative_checkpoints c ON c.checkpoint_id=e.checkpoint_id "
        "WHERE e.chat_id=OLD.chat_id AND e.session_id=OLD.session_id AND OLD.id<=c.through_rowid) "
        "BEGIN SELECT RAISE(ABORT,'The pre-finale transcript checkpoint is immutable'); END"
    )
    db.execute(
        "CREATE TRIGGER pre_finale_prefix_insert BEFORE INSERT ON messages "
        "WHEN NEW.id>0 AND EXISTS(SELECT 1 FROM ending_state e JOIN narrative_checkpoints c "
        "ON c.checkpoint_id=e.checkpoint_id WHERE e.chat_id=NEW.chat_id AND e.session_id=NEW.session_id "
        "AND NEW.id<=c.through_rowid) "
        "BEGIN SELECT RAISE(ABORT,'The pre-finale transcript checkpoint is immutable'); END"
    )
