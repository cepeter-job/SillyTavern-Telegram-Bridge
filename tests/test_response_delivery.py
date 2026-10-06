import json
import sqlite3
import unittest
from unittest.mock import patch

from settings_test_support import SettingsTestCase

import bridge.response_delivery as response_delivery
import bridge.telegram as telegram
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

    def test_send_reply_renders_native_formatting_entities(self):
        requests = []

        def request(_token, method, payload):
            requests.append((method, payload))
            return {"message_id": 81}

        with patch.object(telegram, "telegram_request", side_effect=request):
            response_delivery.send_reply(
                "token",
                "chat",
                "<b>Bold</b> <u>Under</u> <s>Strike</s> <tg-spoiler>Secret</tg-spoiler>",
                app_settings=self.app_settings_builder.build(),
            )

        self.assertEqual(requests[0][1]["text"], "Bold Under Strike Secret")
        self.assertEqual(
            requests[0][1]["entities"],
            [
                {"type": "bold", "offset": 0, "length": 4},
                {"type": "underline", "offset": 5, "length": 5},
                {"type": "strikethrough", "offset": 11, "length": 6},
                {"type": "spoiler", "offset": 18, "length": 6},
            ],
        )

    def test_send_reply_supports_nested_entities_with_utf16_offsets(self):
        requests = []

        def request(_token, method, payload):
            requests.append((method, payload))
            return {"message_id": 82}

        with patch.object(telegram, "telegram_request", side_effect=request):
            response_delivery.send_reply(
                "token",
                "chat",
                "🙂 <tg-spoiler><b>Secret</b></tg-spoiler>",
                app_settings=self.app_settings_builder.build(),
            )

        self.assertEqual(requests[0][1]["text"], "🙂 Secret")
        self.assertCountEqual(
            requests[0][1]["entities"],
            [
                {"type": "spoiler", "offset": 3, "length": 6},
                {"type": "bold", "offset": 3, "length": 6},
            ],
        )

    def test_send_reply_renders_text_link_entity(self):
        requests = []

        def request(_token, method, payload):
            requests.append((method, payload))
            return {"message_id": 85}

        with patch.object(telegram, "telegram_request", side_effect=request):
            response_delivery.send_reply(
                "token",
                "chat",
                '<a href="https://example.com/reference">Docs</a>',
                app_settings=self.app_settings_builder.build(),
            )

        self.assertEqual(requests[0][1]["text"], "Docs")
        self.assertEqual(
            requests[0][1]["entities"],
            [
                {
                    "type": "text_link",
                    "offset": 0,
                    "length": 4,
                    "url": "https://example.com/reference",
                }
            ],
        )

    def test_send_reply_renders_code_pre_and_blockquotes(self):
        requests = []

        def request(_token, method, payload):
            requests.append((method, payload))
            return {"message_id": 83}

        source = (
            "<code>code</code>\n<pre>block</pre>\n<blockquote>Quote</blockquote>\n"
            "<blockquote expandable>Hidden quote</blockquote>"
        )
        with patch.object(telegram, "telegram_request", side_effect=request):
            response_delivery.send_reply(
                "token",
                "chat",
                source,
                app_settings=self.app_settings_builder.build(),
            )

        self.assertEqual(requests[0][1]["text"], "code\nblock\nQuote\nHidden quote")
        self.assertEqual(
            requests[0][1]["entities"],
            [
                {"type": "code", "offset": 0, "length": 4},
                {"type": "pre", "offset": 5, "length": 5},
                {"type": "blockquote", "offset": 11, "length": 5},
                {"type": "expandable_blockquote", "offset": 17, "length": 12},
            ],
        )

    def test_send_reply_preserves_spoiler_across_chunk_boundary(self):
        requests = []

        def request(_token, method, payload):
            requests.append((method, payload))
            return {"message_id": 90 + len(requests)}

        source = "<tg-spoiler>" + ("A" * 4100) + "</tg-spoiler>"
        with patch.object(telegram, "telegram_request", side_effect=request):
            response_delivery.send_reply(
                "token",
                "chat",
                source,
                app_settings=self.app_settings_builder.build(),
            )

        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0][1]["text"], "A" * 4000)
        self.assertEqual(requests[0][1]["entities"], [{"type": "spoiler", "offset": 0, "length": 4000}])
        self.assertEqual(requests[1][1]["text"], "A" * 100)
        self.assertEqual(requests[1][1]["entities"], [{"type": "spoiler", "offset": 0, "length": 100}])

    def test_send_reply_keeps_general_markdown_literal(self):
        requests = []

        def request(_token, method, payload):
            requests.append((method, payload))
            return {"message_id": 84}

        source = "**Bold** ||Secret|| __Under__ ~~Strike~~ [Docs](https://example.com/reference)"
        with patch.object(telegram, "telegram_request", side_effect=request):
            response_delivery.send_reply(
                "token",
                "chat",
                source,
                app_settings=self.app_settings_builder.build(),
            )

        self.assertEqual(requests[0][1]["text"], source)
        self.assertNotIn("entities", requests[0][1])

    def test_send_reply_renders_single_star_narration_as_telegram_italic(self):
        requests = []

        def request(_token, method, payload):
            requests.append((method, payload))
            return {"message_id": 73}

        with patch.object(telegram, "telegram_request", side_effect=request):
            response_delivery.send_reply(
                "token",
                "chat",
                '*She looks away.*\n\n"I saw her yesterday."',
                app_settings=self.app_settings_builder.build(),
            )

        self.assertEqual(
            requests,
            [
                (
                    "sendMessage",
                    {
                        "chat_id": "chat",
                        "text": 'She looks away.\n\n"I saw her yesterday."',
                        "disable_web_page_preview": True,
                        "entities": [{"type": "italic", "offset": 0, "length": 15}],
                    },
                )
            ],
        )

    def test_send_reply_keeps_single_star_emphasis_inside_dialogue_normal(self):
        for source, expected in (
            ('*Narration.* "I *really* mean it."', 'Narration. "I really mean it."'),
            ("*Narration.* “I *really* mean it.”", "Narration. “I really mean it.”"),
        ):
            with self.subTest(source=source):
                requests = []

                def request(_token, method, payload, sink=requests):
                    sink.append((method, payload))
                    return {"message_id": 76}

                with patch.object(telegram, "telegram_request", side_effect=request):
                    response_delivery.send_reply(
                        "token",
                        "chat",
                        source,
                        app_settings=self.app_settings_builder.build(),
                    )

                self.assertEqual(requests[0][1]["text"], expected)
                self.assertEqual(requests[0][1]["entities"], [{"type": "italic", "offset": 0, "length": 10}])

    def test_send_reply_excludes_quoted_speech_from_a_narration_marker(self):
        requests = []

        def request(_token, method, payload):
            requests.append((method, payload))
            return {"message_id": 77}

        with patch.object(telegram, "telegram_request", side_effect=request):
            response_delivery.send_reply(
                "token",
                "chat",
                '*She says "I mean it." softly.*',
                app_settings=self.app_settings_builder.build(),
            )

        self.assertEqual(requests[0][1]["text"], 'She says "I mean it." softly.')
        self.assertEqual(
            requests[0][1]["entities"],
            [
                {"type": "italic", "offset": 0, "length": 9},
                {"type": "italic", "offset": 21, "length": 8},
            ],
        )

    def test_send_reply_preserves_non_roleplay_star_sequences(self):
        requests = []

        def request(_token, method, payload):
            requests.append((method, payload))
            return {"message_id": 75}

        source = "Literal **bold** stays; * unfinished; trailing *"
        with patch.object(telegram, "telegram_request", side_effect=request):
            response_delivery.send_reply(
                "token",
                "chat",
                source,
                app_settings=self.app_settings_builder.build(),
            )

        self.assertEqual(requests[0][1]["text"], source)
        self.assertNotIn("entities", requests[0][1])

    def test_send_reply_uses_utf16_offsets_for_italic_entities(self):
        requests = []

        def request(_token, method, payload):
            requests.append((method, payload))
            return {"message_id": 74}

        with patch.object(telegram, "telegram_request", side_effect=request):
            response_delivery.send_reply(
                "token",
                "chat",
                '🙂 "Hi." *She smiles.*',
                app_settings=self.app_settings_builder.build(),
            )

        self.assertEqual(requests[0][1]["text"], '🙂 "Hi." She smiles.')
        self.assertEqual(requests[0][1]["entities"], [{"type": "italic", "offset": 9, "length": 11}])

    def test_send_reply_keeps_italic_style_when_span_crosses_chunk_boundary(self):
        requests = []

        def request(_token, method, payload):
            requests.append((method, payload))
            return {"message_id": 80 + len(requests)}

        source = "*" + ("A" * 4100) + '* "Done."'
        with patch.object(telegram, "telegram_request", side_effect=request):
            response_delivery.send_reply(
                "token",
                "chat",
                source,
                app_settings=self.app_settings_builder.build(),
            )

        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0][1]["text"], "A" * 3999)
        self.assertEqual(requests[0][1]["entities"], [{"type": "italic", "offset": 0, "length": 3999}])
        self.assertEqual(requests[1][1]["text"], ("A" * 101) + ' "Done."')
        self.assertEqual(requests[1][1]["entities"], [{"type": "italic", "offset": 0, "length": 101}])

    def test_send_reply_applies_italic_entities_to_streaming_preview(self):
        requests = []

        def edit_existing(_token, method, payload):
            requests.append((method, payload))
            raise RuntimeError("Telegram editMessageText failed: Bad Request: message is not modified")

        with patch.object(response_delivery, "telegram_request", side_effect=edit_existing):
            response_delivery.send_reply(
                "token",
                "chat",
                '*Final narration.* "Hi."',
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
                        "text": 'Final narration. "Hi."',
                        "disable_web_page_preview": True,
                        "entities": [{"type": "italic", "offset": 0, "length": 16}],
                    },
                )
            ],
        )

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
