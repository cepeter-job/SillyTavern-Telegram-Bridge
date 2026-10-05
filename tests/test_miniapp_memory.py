import base64
import threading

import pytest
from miniapp_test_support import identity, make_services


def setup(tmp_path):
    from bridge.embedding_port import EmbeddingPort
    from bridge.miniapp_context import current_session
    from bridge.rag_composition import build_rag_service

    services = make_services(tmp_path)
    services.rag = build_rag_service(
        app_settings=services.config,
        embedding_port=EmbeddingPort(embed_backend=lambda text: None, batch_backend=lambda texts: [None] * len(texts)),
    )
    who = identity()
    session = current_session(services, who, {})["session"]
    return services, who, {"session_id": session["session_id"]}


def test_memory_settings_and_manual_summary_are_private_and_revision_checked(tmp_path):
    from bridge.miniapp_memory import memory_settings, memory_status, save_summary

    s, w, p = setup(tmp_path)
    before = memory_status(s, w, {})
    assert before["scope"] == "session"
    memory_settings(s, w, {**p, "mode": "off"})
    assert memory_status(s, w, {})["mode"] == "off"
    save_summary(s, w, {**p, "summary": "We visited the garden.", "digest": before["summary_digest"]})
    assert memory_status(s, w, {})["summary"] == "We visited the garden."
    with pytest.raises(ValueError):
        save_summary(s, w, {**p, "summary": "stale", "digest": before["summary_digest"]})
    assert memory_status(s, identity("67890"), {})["summary"] == ""
    with pytest.raises(ValueError):
        memory_settings(s, w, {**p, "mode": "on", "scope": "global"})


def test_curated_memory_editor_has_explicit_bounds_and_no_foreign_leak(tmp_path):
    from bridge.miniapp_memory import memory_status, save_curated

    s, w, p = setup(tmp_path)
    before = memory_status(s, w, {})
    item = {"key": "garden", "text": "Garden visited.", "kind": "event", "confidence": 1.0}
    save_curated(s, w, {**p, "items": [item], "digest": before["curated_digest"]})
    assert memory_status(s, w, {})["curated"][0]["text"] == "Garden visited."
    assert memory_status(s, identity("67890"), {})["curated"] == []
    current = memory_status(s, w, {})
    with pytest.raises(ValueError):
        save_curated(s, w, {**p, "items": [item] * 25, "digest": current["curated_digest"]})
    save_curated(s, w, {**p, "items": [], "digest": current["curated_digest"]})
    assert memory_status(s, w, {})["curated"] == []


def test_manual_curated_save_invalidates_inflight_extraction(tmp_path, monkeypatch):
    from application_test_setup import make_test_provider_port

    from bridge import memory_curator
    from bridge.miniapp_memory import memory_status, save_curated

    s, w, p = setup(tmp_path)
    db = s.db_factory()
    db.execute(
        "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
        (w.chat_id, p["session_id"], "user", "Obsolete source", 1.0),
    )
    db.commit()
    db.close()
    started = threading.Event()
    release = threading.Event()
    errors = []
    retained = []

    def generate(*_args, **_kwargs):
        started.set()
        assert release.wait(5)
        return '{"memories":[{"key":"obsolete","text":"Obsolete","kind":"fact"}]}'

    def worker():
        db = s.db_factory()
        try:
            session = memory_status(s, w, {})["session"]
            memory_curator.curate_memory_now(
                db,
                "",
                w.chat_id,
                session,
                "Alice",
                provider_port=make_test_provider_port(generate_backend=generate),
                app_settings=s.config,
            )
        except BaseException as error:
            errors.append(error)
        finally:
            db.close()

    monkeypatch.setattr(memory_curator, "_retain_with_client", lambda *a, **k: retained.append(a))
    before = memory_status(s, w, {})
    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert started.wait(5)
        item = {"key": "reviewed", "text": "Reviewed", "kind": "fact", "confidence": 1.0}
        save_curated(s, w, {**p, "items": [item], "digest": before["curated_digest"]})
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert errors == []
    assert memory_status(s, w, {})["curated"] == [item]
    assert retained == []


def test_summary_regeneration_reports_partial_completion(tmp_path):
    from application_test_setup import make_test_provider_port

    from bridge.miniapp_memory import memory_status, regenerate_summary

    s, w, p = setup(tmp_path)
    db = s.db_factory()
    for row in range(40):
        db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            (w.chat_id, p["session_id"], "user", "x" * 2000, float(row)),
        )
    db.commit()
    db.close()
    calls = []

    def generate(*_args, **_kwargs):
        calls.append(True)
        if len(calls) == 2:
            raise RuntimeError("synthetic second segment failure")
        return '{"blocks":[{"text":"Processed prefix","visibility":"shared","known_by":[]}]}'

    s.provider = make_test_provider_port(generate_backend=generate)
    result = regenerate_summary(s, w, p)
    assert result["complete"] is False
    assert 0 < result["summary_through"] < 40
    assert result["summary_through"] == memory_status(s, w, {})["summary_through"]


def test_summary_regeneration_failure_does_not_claim_old_full_summary_was_refreshed(tmp_path):
    from application_test_setup import make_test_provider_port

    from bridge.miniapp_memory import regenerate_summary

    s, w, p = setup(tmp_path)
    db = s.db_factory()
    rowid = db.execute(
        "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
        (w.chat_id, p["session_id"], "user", "Source", 1.0),
    ).lastrowid
    db.execute(
        "INSERT INTO session_summaries VALUES(?,?,?,?,?)", (w.chat_id, p["session_id"], "Prior summary", rowid, 1.0)
    )
    db.commit()
    db.close()

    def generate(*_args, **_kwargs):
        raise RuntimeError("synthetic provider failure")

    s.provider = make_test_provider_port(generate_backend=generate)
    result = regenerate_summary(s, w, p)
    assert result["complete"] is False
    assert result["summary"] == "Prior summary"
    assert result["summary_through"] == rowid


def test_databank_upload_versions_search_and_removal_use_real_rag(tmp_path):
    from bridge.miniapp_memory import (
        activate_version,
        databank,
        document_versions,
        remove_document,
        search_documents,
        upload_document,
    )

    s, w, p = setup(tmp_path)
    upload_document(
        s, w, {**p, "filename": "garden.txt", "data": base64.b64encode(b"The garden gate opens at noon.").decode()}
    )
    upload_document(
        s, w, {**p, "filename": "garden.txt", "data": base64.b64encode(b"The garden gate opens at dawn.").decode()}
    )
    docs = databank(s, w, {})["documents"]
    assert len(docs) == 1 and docs[0]["filename"] == "garden.txt"
    versions = document_versions(s, w, {"filename": "garden.txt"})["versions"]
    assert len(versions) == 2
    activate_version(s, w, {**p, "filename": "garden.txt", "version": 1, "confirm": True})
    results = search_documents(s, w, {**p, "query": "garden gate"})["results"]
    assert any("noon" in item["text"] for item in results)
    assert databank(s, identity("67890"), {})["documents"] == []
    with pytest.raises(ValueError):
        remove_document(s, w, {**p, "filename": "garden.txt"})
    remove_document(s, w, {**p, "filename": "garden.txt", "confirm": True})
    assert databank(s, w, {})["documents"] == []


@pytest.mark.parametrize("filename", ["../secret.txt", "unsafe.exe", "/tmp/x.txt"])
def test_databank_upload_rejects_unsafe_or_unsupported_files(tmp_path, filename):
    from bridge.miniapp_memory import upload_document

    s, w, p = setup(tmp_path)
    with pytest.raises(ValueError):
        upload_document(s, w, {**p, "filename": filename, "data": "aGVsbG8="})


def test_hindsight_query_and_character_are_not_swapped(tmp_path, monkeypatch):
    import bridge.miniapp_memory as memory

    s, w, p = setup(tmp_path)
    seen = []

    def recalled(db, chat_id, session, query, character_name="", **kwargs):
        seen.append((query, character_name))
        return [{"text": "Remembered the garden."}]

    monkeypatch.setattr(memory, "recall_memory_results", recalled)
    result = memory.recall_remote(s, w, {**p, "query": "garden"})
    assert seen == [("garden", "Default")]
    assert result["results"] == ["Remembered the garden."]
