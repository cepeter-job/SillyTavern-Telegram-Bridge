import json
import sqlite3
import unittest
from unittest.mock import patch

from settings_test_support import SettingsTestCase

import bridge.response_delivery as response_delivery
from bridge.schema import initialize_database_schema


class ResponseDeliveryTests(SettingsTestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        initialize_database_schema(self.db)
        self.db.execute(
            "INSERT INTO messages(rowid,chat_id,session_id,role,content,created_at) "
            "VALUES(42,'chat','s','assistant','stored',1)"
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _send(self, _token, _chat, text, *, acknowledged_chunk=None):
        self.sent.append(text)
        if acknowledged_chunk:
            acknowledged_chunk(72)
        return [72]

    def test_send_reply_sanitizes_stored_html_at_delivery_boundary(self):
        sent = []
        self.sent = sent
        with patch.object(
            response_delivery,
            "send_text",
            side_effect=self._send,
        ):
            response_delivery.send_reply(
                "token",
                "chat",
                "<div>Recovered<br>reply</div>",
                app_settings=self.app_settings_builder.build(),
            )

        self.assertEqual(sent, ["Recovered\nreply"])

    def test_send_reply_reuses_streaming_preview_as_final_message(self):
        requests = []
        sent = []
        self.sent = sent

        def edit_existing(_token, method, payload):
            requests.append((method, payload))
            raise RuntimeError("Telegram editMessageText failed: Bad Request: message is not modified")

        with (
            patch.object(
                response_delivery,
                "telegram_request",
                side_effect=edit_existing,
            ),
            patch.object(
                response_delivery,
                "send_text",
                side_effect=self._send,
            ),
        ):
            response_delivery.send_reply(
                "token",
                "chat",
                "Final response",
                self.db,
                None,
                42,
                replace_message_id=71,
                app_settings=self.app_settings_builder.build(),
            )

        self.assertEqual(
            requests,
            [
                (
                    "editMessageText",
                    {
                        "chat_id": "chat",
                        "message_id": 71,
                        "text": "Final response",
                        "disable_web_page_preview": True,
                    },
                )
            ],
        )
        self.assertEqual(sent, [])
        self.assertEqual(
            json.loads(self.db.execute("SELECT telegram_message_ids FROM messages WHERE rowid=42").fetchone()[0]), [71]
        )

    def test_send_reply_falls_back_when_streaming_preview_is_gone(self):
        sent = []
        self.sent = sent
        with (
            patch.object(
                response_delivery,
                "telegram_request",
                side_effect=RuntimeError("Telegram editMessageText failed: Bad Request: message to edit not found"),
            ),
            patch.object(
                response_delivery,
                "send_text",
                side_effect=self._send,
            ),
        ):
            response_delivery.send_reply(
                "token",
                "chat",
                "Final response",
                self.db,
                None,
                42,
                replace_message_id=71,
                app_settings=self.app_settings_builder.build(),
            )

        self.assertEqual(sent, ["Final response"])
        self.assertEqual(
            json.loads(self.db.execute("SELECT telegram_message_ids FROM messages WHERE rowid=42").fetchone()[0]), [72]
        )

    def test_send_reply_reuses_preview_then_sends_only_remaining_chunks(self):
        requests = []
        sent = []
        self.sent = sent
        reply = "A" * 4100

        with (
            patch.object(
                response_delivery,
                "telegram_request",
                side_effect=lambda _token, method, payload: requests.append((method, payload)) or {},
            ),
            patch.object(
                response_delivery,
                "send_text",
                side_effect=self._send,
            ),
        ):
            response_delivery.send_reply(
                "token",
                "chat",
                reply,
                self.db,
                None,
                42,
                replace_message_id=71,
                app_settings=self.app_settings_builder.build(),
            )

        self.assertEqual(len(requests[0][1]["text"]), 4000)
        self.assertEqual(sent, ["A" * 100])
        self.assertEqual(
            json.loads(self.db.execute("SELECT telegram_message_ids FROM messages WHERE rowid=42").fetchone()[0]),
            [71, 72],
        )


if __name__ == "__main__":
    unittest.main()
