"""Conservative classification of public document-list provenance."""

from bridge.memory_identity import hindsight_session_prefix


def cleanup_candidate(item, session_id, generation_tag):
    document_id = item.get("id") if isinstance(item, dict) else getattr(item, "id", None)
    tags = item.get("tags") if isinstance(item, dict) else getattr(item, "tags", None)
    if not document_id or not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        return str(document_id or ""), None
    document_id = str(document_id)
    if "st-memory-v2" in tags:
        generations = {tag for tag in tags if tag.startswith("st-generation:")}
        return document_id, generations == {generation_tag}
    prefix = hindsight_session_prefix(session_id)
    return document_id, (
        f"session:{session_id}" in tags
        or document_id == f"st-session-{session_id}"
        or document_id == prefix
        or document_id.startswith(prefix + "-")
    )
