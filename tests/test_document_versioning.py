import tempfile
import unittest
from pathlib import Path

from application_test_setup import ensure_application_extensions, make_native_test_embedding_port
from settings_test_support import SettingsTestCase

import bridge.document_extraction as _owner_document_extraction
import bridge.embedding_transport as _owner_embedding_transport
import bridge.language as _m_language
import bridge.memory_curator as _m_memory_curator
import bridge.rag_indexing as _owner_rag_indexing
import bridge.rag_query as _owner_rag_query
import bridge.rag_repository as _owner_rag_repository
import bridge.session_core as _owner_session_core
import bridge.session_naming as _m_session_naming

ensure_application_extensions()


class DocumentVersioningTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = self.app_settings_builder.db_file
        self.app_settings_builder.db_file = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect(app_settings=self.app_settings_builder.build())
        self.old_batch = _owner_embedding_transport.embed_rag_batch
        self.old_cached = _owner_rag_query.cached_rag_embedding
        _owner_embedding_transport.embed_rag_batch = lambda texts, *, app_settings=None: [None] * len(texts)
        _owner_rag_query.cached_rag_embedding = lambda _db, _text, *, app_settings=None, embedding_port: None

    def tearDown(self):
        _owner_embedding_transport.embed_rag_batch = self.old_batch
        _owner_rag_query.cached_rag_embedding = self.old_cached
        self.db.close()
        self.app_settings_builder.db_file = self.old_db
        self.tmp.cleanup()

    def test_same_filename_creates_new_active_version(self):
        status1, chunks1 = _owner_rag_indexing.add_data_bank_document(
            self.db,
            "chat",
            "notes.txt",
            b"alpha old version",
            app_settings=self.app_settings_builder.build(),
            embedding_port=make_native_test_embedding_port(app_settings=self.app_settings_builder.build()),
        )
        status2, chunks2 = _owner_rag_indexing.add_data_bank_document(
            self.db,
            "chat",
            "notes.txt",
            b"beta new version",
            app_settings=self.app_settings_builder.build(),
            embedding_port=make_native_test_embedding_port(app_settings=self.app_settings_builder.build()),
        )

        self.assertEqual(status1, "added")
        self.assertEqual(status2, "versioned")
        self.assertGreater(chunks1, 0)
        self.assertGreater(chunks2, 0)

        versions = _owner_rag_repository.data_bank_document_versions(self.db, "chat", "notes.txt")
        self.assertEqual([row[1] for row in versions], [2, 1])
        self.assertEqual([row[2] for row in versions], [1, 0])

        active = _owner_rag_repository.data_bank_documents(self.db, "chat")
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0][1], "notes.txt")

    def test_exact_content_reupload_stays_duplicate(self):
        payload = b"same exact document"
        self.assertEqual(
            _owner_rag_indexing.add_data_bank_document(
                self.db,
                "chat",
                "notes.txt",
                payload,
                app_settings=self.app_settings_builder.build(),
                embedding_port=make_native_test_embedding_port(app_settings=self.app_settings_builder.build()),
            )[0],
            "added",
        )
        self.assertEqual(
            _owner_rag_indexing.add_data_bank_document(
                self.db,
                "chat",
                "renamed.txt",
                payload,
                app_settings=self.app_settings_builder.build(),
                embedding_port=make_native_test_embedding_port(app_settings=self.app_settings_builder.build()),
            )[0],
            "duplicate",
        )
        count = self.db.execute("SELECT COUNT(*) FROM data_bank_documents WHERE chat_id='chat'").fetchone()[0]
        self.assertEqual(count, 1)

    def test_retrieval_uses_only_active_version_and_rollback_is_atomic(self):
        _owner_rag_indexing.add_data_bank_document(
            self.db,
            "chat",
            "story.txt",
            b"ancient dragon sleeps beneath mountain",
            app_settings=self.app_settings_builder.build(),
            embedding_port=make_native_test_embedding_port(app_settings=self.app_settings_builder.build()),
        )
        _owner_rag_indexing.add_data_bank_document(
            self.db,
            "chat",
            "story.txt",
            b"modern spaceship waits above city",
            app_settings=self.app_settings_builder.build(),
            embedding_port=make_native_test_embedding_port(app_settings=self.app_settings_builder.build()),
        )

        current = _owner_rag_query.retrieve_data_bank(
            self.db,
            "chat",
            "spaceship",
            app_settings=self.app_settings_builder.build(),
            embedding_port=make_native_test_embedding_port(app_settings=self.app_settings_builder.build()),
        )
        old_hidden = _owner_rag_query.retrieve_data_bank(
            self.db,
            "chat",
            "dragon",
            app_settings=self.app_settings_builder.build(),
            embedding_port=make_native_test_embedding_port(app_settings=self.app_settings_builder.build()),
        )
        self.assertTrue(current)
        self.assertEqual(old_hidden, [])

        self.assertTrue(_owner_rag_indexing.activate_data_bank_version(self.db, "chat", "story.txt", 1))
        old_visible = _owner_rag_query.retrieve_data_bank(
            self.db,
            "chat",
            "dragon",
            app_settings=self.app_settings_builder.build(),
            embedding_port=make_native_test_embedding_port(app_settings=self.app_settings_builder.build()),
        )
        new_hidden = _owner_rag_query.retrieve_data_bank(
            self.db,
            "chat",
            "spaceship",
            app_settings=self.app_settings_builder.build(),
            embedding_port=make_native_test_embedding_port(app_settings=self.app_settings_builder.build()),
        )
        self.assertTrue(old_visible)
        self.assertEqual(new_hidden, [])

        active_count = self.db.execute(
            "SELECT COUNT(*) FROM data_bank_documents WHERE chat_id='chat' AND filename='story.txt' AND active=1"
        ).fetchone()[0]
        self.assertEqual(active_count, 1)

    def test_activate_unknown_version_does_not_change_active_version(self):
        _owner_rag_indexing.add_data_bank_document(
            self.db,
            "chat",
            "notes.txt",
            b"version one",
            app_settings=self.app_settings_builder.build(),
            embedding_port=make_native_test_embedding_port(app_settings=self.app_settings_builder.build()),
        )
        _owner_rag_indexing.add_data_bank_document(
            self.db,
            "chat",
            "notes.txt",
            b"version two",
            app_settings=self.app_settings_builder.build(),
            embedding_port=make_native_test_embedding_port(app_settings=self.app_settings_builder.build()),
        )
        self.assertFalse(_owner_rag_indexing.activate_data_bank_version(self.db, "chat", "notes.txt", 99))
        versions = _owner_rag_repository.data_bank_document_versions(self.db, "chat", "notes.txt")
        active = [row[1] for row in versions if row[2]]
        self.assertEqual(active, [2])


ensure_application_extensions()


class PdfWorkerTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app_settings_builder.db_file = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect(app_settings=self.app_settings_builder.build())

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_pdf_extraction_uses_bounded_worker(self):
        text = "Hello PDF worker"
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
                b"/Resources << /Font << /F1 5 0 R >> >> >>"
            ),
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        ]
        pdf = bytearray(b"%PDF-1.4\n")
        offsets = [0]
        for index, obj in enumerate(objects, 1):
            offsets.append(len(pdf))
            pdf.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
        xref = len(pdf)
        pdf.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
        pdf.extend(b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:]))
        pdf.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())

        self.assertIn(
            text,
            _owner_document_extraction.extract_data_bank_text(
                "fixture.pdf", bytes(pdf), app_settings=self.app_settings_builder.build()
            ),
        )
        with self.assertRaises(ValueError):
            _owner_document_extraction.extract_data_bank_text(
                "fixture.pdf", b"not a pdf", app_settings=self.app_settings_builder.build()
            )

        first = _owner_session_core.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )
        _m_language.set_response_language(
            self.db,
            "chat",
            first["session_id"],
            "id",
            update_session=_owner_session_core.update_session,
        )
        second = _m_session_naming.create_session(
            self.db,
            "chat",
            self.app_settings_builder.default_model,
            session_id="second",
            app_settings=self.app_settings_builder.build(),
        )

        self.assertEqual(
            _m_memory_curator.load_session(
                self.db,
                "chat",
                first["session_id"],
                self.app_settings_builder.default_model,
                app_settings=self.app_settings_builder.build(),
            )["response_language"],
            "id",
        )
        self.assertEqual(second["response_language"], "auto")


if __name__ == "__main__":
    unittest.main()
