"""Bounded ranked lexical candidates from the complete authorized corpus.

FTS5 unicode61 matches tokens, with Unicode case/diacritic normalization but no
stemming or language-aware word segmentation. Hindsight supplies semantic recall.
The index is rebuildable; exact canonical/payload checks still precede rendering.
"""

from __future__ import annotations

import json
import sqlite3

from bridge.memory_contracts import MemoryReadScope, ordered_relevance_terms
from bridge.memory_store import request_source_cutoff

MAX_SEARCH_CANDIDATES = 48

# Fixed SQL fragments only. Authorization precedes ORDER BY/LIMIT so an unrelated
# branch, later grant, or private high-ranked fact cannot crowd out valid evidence.
_AUTHORIZED_FROM = (
    " JOIN memory_fact_provenance p ON p.memory_id=e.memory_id AND p.valid=1 "
    "JOIN episodic_memory_visibility v ON v.memory_id=e.memory_id "
    "AND v.chat_id=e.chat_id AND v.session_id=e.session_id "
    "JOIN sessions s ON s.chat_id=e.chat_id AND s.session_id=e.session_id AND s.created_at=p.session_created_at "
    "LEFT JOIN memory_segments g ON g.document_id=p.source_document_id AND g.valid=1 "
    "LEFT JOIN memory_layer_state l ON l.chat_id=g.chat_id AND l.session_id=g.session_id "
    "AND l.session_created_at=g.session_created_at AND l.layer=g.layer "
    "LEFT JOIN messages m ON m.id=g.start_id AND m.chat_id=g.chat_id AND m.session_id=g.session_id "
    "LEFT JOIN memory_explicit_events x ON x.event_id=p.explicit_event_id AND x.valid=1 "
)
_AUTHORIZED_WHERE = (
    "p.chat_id=? AND p.session_id=? AND p.session_created_at=? "
    "AND e.chat_id=p.chat_id AND e.session_id=p.session_id "
    "AND ((p.provenance_kind='transcript_part' AND g.layer='episodes' "
    "AND g.chat_id=p.chat_id AND g.session_id=p.session_id AND g.session_created_at=p.session_created_at "
    "AND g.end_id<=? AND l.purge_epoch=g.purge_epoch AND m.id IS NOT NULL AND length(m.content)>=g.end_offset) "
    "OR (p.provenance_kind='explicit' AND x.chat_id=p.chat_id AND x.session_id=p.session_id "
    "AND x.session_created_at=p.session_created_at AND x.event_id<=? AND (?=0 OR x.accepted_after_rowid<?))) "
    "AND json_valid(v.known_by_json) AND json_type(v.known_by_json)='array' "
    "AND NOT EXISTS(SELECT 1 FROM json_each(v.known_by_json) a WHERE a.type!='text' OR trim(a.value)='') "
    "AND ((v.visibility='shared' AND json_array_length(v.known_by_json)=0) "
    "OR (v.visibility='restricted' AND json_array_length(v.known_by_json)>0)) "
    "AND (?='narrator' OR (?!=0 AND (v.visibility='shared' OR NOT EXISTS("
    "SELECT 1 FROM json_each(?) r WHERE NOT EXISTS(SELECT 1 FROM json_each(v.known_by_json) a "
    "WHERE a.value=r.value))))) "
)
_LEXICAL_SQL = (
    "SELECT e.memory_id FROM memory_episode_fts CROSS JOIN episodic_memories e ON e.memory_id=memory_episode_fts.rowid"  # noqa: S608 -- fixed SQL fragments; all values are bound
    # Pin FTS first: otherwise SQLite may probe the index once per unrelated fact.
    + _AUTHORIZED_FROM.replace(" JOIN ", " CROSS JOIN ").replace("LEFT CROSS JOIN", "LEFT JOIN")
    + "WHERE memory_episode_fts MATCH ? AND "
    + _AUTHORIZED_WHERE
    + "ORDER BY bm25(memory_episode_fts),e.importance DESC,e.memory_id DESC LIMIT ?"
)
_EXPLICIT_SQL = (
    "SELECT e.memory_id FROM episodic_memories e"  # noqa: S608 -- fixed SQL fragments; all values are bound
    + _AUTHORIZED_FROM
    + "WHERE p.provenance_kind='explicit' AND "
    + _AUTHORIZED_WHERE
    + "ORDER BY e.importance DESC,e.memory_id DESC LIMIT 6"
)


def search_fact_ids(db: sqlite3.Connection, scope: MemoryReadScope, query: str) -> list[int]:
    parameters = (
        scope.chat_id,
        scope.session_id,
        scope.session_created_at,
        request_source_cutoff(db, scope),
        scope.explicit_event_cutoff,
        int(scope.historical),
        scope.through_rowid,
        scope.consumer,
        len(scope.principals),
        json.dumps(scope.principals),
    )
    terms = ordered_relevance_terms(query, limit=24)
    ids: list[int] = []
    if terms:
        expression = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
        ids.extend(int(row[0]) for row in db.execute(_LEXICAL_SQL, (expression, *parameters, MAX_SEARCH_CANDIDATES)))
    ids.extend(int(row[0]) for row in db.execute(_EXPLICIT_SQL, parameters))
    return list(dict.fromkeys(ids))
