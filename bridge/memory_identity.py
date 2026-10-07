"""Pure document provenance shared by retention and durable erasure."""

import hashlib
import json
import re


def hindsight_session_prefix(session_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(session_id)).strip("-.")[:80] or "session"
    digest = hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()[:12]
    return f"st-session-{safe}-{digest}"


def hindsight_generation_tags(
    chat_id: str, session_id: str, session_created_at: float, external_epoch: int
) -> list[str]:
    identity = json.dumps(
        [str(chat_id), str(session_id), float(session_created_at), int(external_epoch)],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return ["st-memory-v2", "st-generation:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()]
