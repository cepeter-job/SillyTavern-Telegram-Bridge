"""Forward-only evidence storage for reconciled arcs; never invokes a model."""

import sqlite3


def migrate_narrative_arc_evidence(db: sqlite3.Connection) -> None:
    if not db.in_transaction:
        raise RuntimeError("Arc evidence migration requires an active transaction")
    db.execute(
        "ALTER TABLE narrative_arcs ADD COLUMN evidence_json TEXT NOT NULL DEFAULT '[]' "
        "CHECK(json_valid(evidence_json) AND length(evidence_json)<=4096)"
    )
