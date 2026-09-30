"""Private session memory and Data Bank use cases, never HTTP or Telegram handlers."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
import time
from pathlib import Path
from typing import Any

from bridge.card_content import card_fields_from_file
from bridge.limits import RAG_MAX_FILE_BYTES, RAG_SUPPORTED_SUFFIXES, SUMMARY_MAX_CHARS
from bridge.memory import generate_session_summary_result, get_session_summary
from bridge.memory_backend import (
    _retain_with_client,
    hindsight_session_lock,
    memory_mode,
    memory_scope,
    recall_memory_results,
)
from bridge.memory_curator import (
    clear_curated_memory_state,
    curate_memory_now,
    curated_memory_document_id,
    get_curated_memory_state,
    memory_curator_key,
)
from bridge.metadata import set_meta
from bridge.miniapp_auth import MiniAppIdentity
from bridge.miniapp_context import digest, require_confirmation, session_scope, text
from bridge.miniapp_errors import MiniAppError
from bridge.miniapp_memory_repository import latest_message, store_summary
from bridge.miniapp_types import ApiRoute
from bridge.sqlite_store import write_transaction


def _revision(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def memory_status(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    with session_scope(services, who, values) as scope:
        summary, covered = get_session_summary(scope.db, scope.chat_id, scope.session["session_id"])
        items, curated_covered = get_curated_memory_state(scope.db, scope.chat_id, scope.session["session_id"])
        return {
            "mode": memory_mode(scope.db, scope.chat_id),
            "scope": memory_scope(scope.db, scope.chat_id),
            "summary": summary,
            "summary_through": covered,
            "summary_digest": _revision([summary, covered]),
            "curated": items,
            "curated_through": curated_covered,
            "curated_digest": _revision([items, curated_covered]),
            "session": scope.session,
            "manual_edit_scope": "local_bridge",
        }


def memory_settings(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    mode = text(values, "mode", 3)
    if mode not in {"on", "off"} or values.get("scope", "session") != "session":
        raise MiniAppError("Memory supports on/off and session-only scope.")
    with session_scope(services, who, values, write=True) as scope:
        set_meta(scope.db, f"memory_mode:{scope.chat_id}", mode)
        return {"saved": True}


def save_summary(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    summary = text(values, "summary", SUMMARY_MAX_CHARS, required=False)
    expected = digest(values)
    with session_scope(services, who, values, write=True) as scope, write_transaction(scope.db):
        previous = get_session_summary(scope.db, scope.chat_id, scope.session["session_id"])
        if _revision(list(previous)) != expected:
            raise MiniAppError("Summary changed. Refresh before saving.", status=409)
        store_summary(
            scope.db,
            scope.chat_id,
            scope.session["session_id"],
            summary,
            latest_message(scope.db, scope.chat_id, scope.session["session_id"]),
            time.time(),
        )
        return {"saved": True}


def _curated_items(values: dict) -> list[dict]:
    items = values.get("items")
    if not isinstance(items, list) or len(items) > 24:
        raise MiniAppError("Curated memory supports up to 24 items.")
    result = []
    keys = set()
    for item in items:
        if not isinstance(item, dict):
            raise MiniAppError("Each memory must be an object.")
        key = text(item, "key", 80)
        kind = text(item, "kind", 32)
        content = text(item, "text", 700)
        confidence = item.get("confidence", 1.0)
        if (
            not re.fullmatch(r"[a-z0-9._:-]+", key)
            or key in keys
            or kind not in {"fact", "relationship", "preference", "promise", "event", "goal"}
            or type(confidence) not in {float, int}
            or not math.isfinite(confidence)
            or not 0 <= confidence <= 1
        ):
            raise MiniAppError("Use unique stable keys, a supported kind, and confidence from 0 to 1.")
        keys.add(key)
        result.append({"key": key, "kind": kind, "text": content, "confidence": confidence})
    return result


def save_curated(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    items = _curated_items(values)
    expected = digest(values)
    with (
        session_scope(services, who, values, write=True) as scope,
        hindsight_session_lock(scope.chat_id, scope.session["session_id"]),
        write_transaction(scope.db),
    ):
        previous = get_curated_memory_state(scope.db, scope.chat_id, scope.session["session_id"])
        if _revision(list(previous)) != expected:
            raise MiniAppError("Curated memory changed. Refresh before saving.", status=409)
        clear_curated_memory_state(scope.db, scope.chat_id, scope.session["session_id"])
        set_meta(
            scope.db,
            memory_curator_key(scope.chat_id, scope.session["session_id"]),
            json.dumps(
                {
                    "items": items,
                    "through_rowid": latest_message(scope.db, scope.chat_id, scope.session["session_id"]),
                    "updated_at": time.time(),
                },
                ensure_ascii=False,
            ),
        )
        return {"saved": True, "remote_sync": "not_performed"}


def _character(services: Any, scope: Any) -> str:
    return str(
        card_fields_from_file(scope.session["character_file"], app_settings=services.config).get("name") or "Character"
    )


def regenerate_summary(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    with session_scope(services, who, values, write=True) as scope:
        result = generate_session_summary_result(
            scope.db,
            scope.chat_id,
            scope.session,
            force=True,
            provider_port=services.provider,
            app_settings=services.config,
        )
        if not result.summary:
            raise MiniAppError("No summary was produced. Check the transcript and Utility model.")
        return {
            "summary": result.summary,
            "summary_through": result.covered_until_rowid,
            "complete": result.complete,
        }


def regenerate_curated(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    with session_scope(services, who, values, write=True) as scope:
        items = curate_memory_now(
            scope.db,
            "",
            scope.chat_id,
            scope.session,
            _character(services, scope),
            provider_port=services.provider,
            app_settings=services.config,
        )
        if items is None:
            raise MiniAppError("No memories were produced. Check the transcript and Utility model.")
        return {"items": items}


def sync_curated(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    require_confirmation(values)
    with session_scope(services, who, values, write=True) as scope:
        items, _covered = get_curated_memory_state(scope.db, scope.chat_id, scope.session["session_id"])
        content = "Curated durable memories:\n" + (
            "\n".join(f"- [{item['kind']}:{item['key']}] {item['text']}" for item in items) if items else "(none)"
        )
        synced = _retain_with_client(
            scope.chat_id,
            scope.session["session_id"],
            curated_memory_document_id(scope.session["session_id"]),
            _character(services, scope),
            content,
            "User-reviewed curated memory",
            "curated",
            "Mini App Hindsight retain unavailable for %s",
            app_settings=services.config,
        )
        if not synced:
            raise MiniAppError("Local memory is saved, but Hindsight synchronization failed. Check its configuration.")
        return {"synced": True}


def purge_remote(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    require_confirmation(values)
    with session_scope(services, who, values, write=True) as scope:
        count = services.memory.purge_session(scope.db, scope.chat_id, scope.session["session_id"])
        return {"purged": count}


def recall_remote(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    query = text(values, "query", 2000)
    with session_scope(services, who, values) as scope:
        results = recall_memory_results(
            scope.db,
            scope.chat_id,
            scope.session,
            app_settings=services.config,
            query=query,
            character_name=_character(services, scope),
        )
        # Return textual memory only, not remote documents, metadata or credentials.
        return {
            "results": [
                str(getattr(item, "text", "") or (item.get("text", "") if isinstance(item, dict) else ""))[:2000]
                for item in results[:10]
            ]
        }


def databank(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    with session_scope(services, who, values) as scope:
        documents = services.rag.documents(scope.db, scope.chat_id)
        total, indexed = services.rag.coverage(scope.db, scope.chat_id)
        return {
            "documents": [{"id": r[0], "filename": r[1], "bytes": r[2], "chunks": r[3]} for r in documents[:200]],
            "total": len(documents),
            "chunks": total,
            "indexed": indexed,
            "mode": services.rag.mode(scope.db, scope.chat_id),
            "session": scope.session,
        }


def rag_settings(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    mode = text(values, "mode", 3)
    if mode not in {"on", "off"}:
        raise MiniAppError("Choose RAG on or off.")
    with session_scope(services, who, values, write=True) as scope:
        set_meta(scope.db, f"rag_mode:{scope.chat_id}", mode)
        return {"saved": True}


def upload_document(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    filename = text(values, "filename", 128)
    if (
        Path(filename).suffix.casefold() not in RAG_SUPPORTED_SUFFIXES
        or filename.startswith(".")
        or ".." in filename
        or not all(ch.isalnum() or ch in " _-." for ch in filename)
    ):
        raise MiniAppError("Choose a supported document with a simple filename.")
    data = text(values, "data", (RAG_MAX_FILE_BYTES + 2) // 3 * 4)
    try:
        raw = base64.b64decode(data, validate=True)
    except (ValueError, binascii.Error):
        raise MiniAppError("Invalid document encoding.") from None
    if len(raw) > RAG_MAX_FILE_BYTES or not raw:
        raise MiniAppError("Upload a nonempty document of at most 10 MB.", status=413)
    with session_scope(services, who, values, write=True) as scope:
        docs = services.rag.documents(scope.db, scope.chat_id)
        if len(docs) >= 200 and not any(row[1] == filename for row in docs):
            raise MiniAppError("Document limit reached. Remove an unused document first.")
        message, chunks = services.rag.add_document(scope.db, scope.chat_id, filename, raw)
        return {"message": message, "chunks": chunks, "filename": filename}


def document_versions(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    with session_scope(services, who, values) as scope:
        versions = services.rag.versions(scope.db, scope.chat_id, text(values, "filename", 128))
        return {
            "versions": [
                {"id": r[0], "version": r[1], "active": bool(r[2]), "bytes": r[3], "chunks": r[4]}
                for r in versions[:100]
            ]
        }


def activate_version(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    require_confirmation(values)
    version = values.get("version")
    if type(version) is not int or not 1 <= version <= 1000000:
        raise MiniAppError("Invalid version number.")
    with session_scope(services, who, values, write=True) as scope:
        if not services.rag.activate(scope.db, scope.chat_id, text(values, "filename", 128), version):
            raise MiniAppError("Document version not found.", status=404)
        return {"activated": True}


def remove_document(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    require_confirmation(values)
    with session_scope(services, who, values, write=True) as scope:
        return {"removed": services.rag.remove(scope.db, scope.chat_id, text(values, "filename", 128))}


def search_documents(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    query = text(values, "query", 2000)
    with session_scope(services, who, values) as scope:
        results = services.rag.retrieve(scope.db, scope.chat_id, query, limit=5)
        return {"results": [{"filename": r[0], "text": r[1][:4000]} for r in results]}


def reindex_documents(services: Any, who: MiniAppIdentity, values: dict) -> dict:
    require_confirmation(values)
    filename = text(values, "filename", 128, required=False) or None
    with session_scope(services, who, values, write=True) as scope:
        total, indexed = services.rag.reindex(scope.db, scope.chat_id, filename)
        return {"total": total, "indexed": indexed}


def routes() -> list[ApiRoute]:
    return [
        ApiRoute("GET", "/memory", memory_status),
        ApiRoute("PATCH", "/memory/settings", memory_settings),
        ApiRoute("PATCH", "/memory/summary", save_summary),
        ApiRoute("PATCH", "/memory/curated", save_curated),
        ApiRoute("POST", "/memory/summary/generate", regenerate_summary, "summary"),
        ApiRoute("POST", "/memory/curated/generate", regenerate_curated, "curate"),
        ApiRoute("POST", "/memory/curated/sync", sync_curated, "curated_sync"),
        ApiRoute("POST", "/memory/purge", purge_remote, "memory_purge"),
        ApiRoute("POST", "/memory/search", recall_remote, "memory_search"),
        ApiRoute("GET", "/databank", databank),
        ApiRoute("PATCH", "/databank/settings", rag_settings),
        ApiRoute("POST", "/databank", upload_document, "rag_upload"),
        ApiRoute("POST", "/databank/search", search_documents, "rag_search"),
        ApiRoute("POST", "/databank/reindex", reindex_documents, "rag_reindex"),
        ApiRoute("GET", "/databank/{filename}/versions", document_versions),
        ApiRoute("POST", "/databank/{filename}/activate", activate_version),
        ApiRoute("DELETE", "/databank/{filename}", remove_document),
    ]
