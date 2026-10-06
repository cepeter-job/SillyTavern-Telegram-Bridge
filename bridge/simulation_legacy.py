"""Import only explicit numeric records quoted from committed legacy assistant ledgers."""

import re
import sqlite3
from typing import Any

from bridge.npc_repository import find_npc_by_name_or_alias


def proven_relationship_baseline(
    db: sqlite3.Connection, chat_id: str, session_id: str, source_rowid: int, item: dict[str, Any]
) -> dict[str, int] | None:
    baseline = item.get("baseline")
    if baseline is None:
        return None
    row = db.execute(
        "SELECT role,content FROM messages WHERE chat_id=? AND session_id=? AND id=?",
        (chat_id, session_id, source_rowid),
    ).fetchone()
    if row is None or row[0] != "assistant":
        raise ValueError("Legacy relationship evidence must be committed assistant state")
    quote = baseline["quote"]
    blocks = re.findall(r"<internal_states\b[^>]*>(.*?)(?:</internal_states\s*>|$)", row[1], re.I | re.S)
    if not any(quote in block for block in blocks):
        raise ValueError("Legacy relationship quote is absent from its canonical source")
    entity = find_npc_by_name_or_alias(db, chat_id, session_id, item["npc"])
    names = [item["npc"], *([entity.display_name, *entity.aliases] if entity else [])]
    suffix = (
        rf"\s*:\s*BOND\s*[:=]\s*{baseline['bond']}\s*[,;|]?\s*"
        rf"Sparks\s*[:=]\s*{baseline['sparks']}\s*[,;|]?\s*Grudge\s*[:=]\s*{baseline['grudge']}\s*"
    )
    if not any(re.fullmatch(re.escape(name) + suffix, quote, re.I) for name in names):
        raise ValueError("Legacy relationship numbers or identity differ from their source quote")
    return {field: baseline[field] for field in ("bond", "sparks", "grudge")}
