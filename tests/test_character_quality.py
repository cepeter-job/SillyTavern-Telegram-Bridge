from application_test_setup import ensure_application_extensions, make_test_provider_port

ensure_application_extensions()

import base64
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

from settings_test_support import SettingsTestCase

import bridge.character_quality as quality
from bridge.card_content import parse_png_chara_bytes
from bridge.memory_curator import db_connect
from bridge.model_router import ModelRoutingError
from bridge.provider_errors import ProviderRequestError


def _raw_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def _metadata_chunk(label: str, card: dict) -> bytes:
    encoded = base64.b64encode(json.dumps(card).encode("utf-8"))
    return _raw_chunk(b"tEXt", label.encode() + b"\x00" + encoded)


def _png_with_metadata(*items: tuple[str, dict]) -> bytes:
    metadata = b"".join(_metadata_chunk(label, card) for label, card in items)
    return b"\x89PNG\r\n\x1a\n" + metadata + _raw_chunk(b"IEND", b"")


def _metadata_cards(raw: bytes) -> list[tuple[str, dict]]:
    pos, cards = 8, []
    while pos < len(raw):
        size = struct.unpack(">I", raw[pos : pos + 4])[0]
        kind = raw[pos + 4 : pos + 8]
        data = raw[pos + 8 : pos + 8 + size]
        pos += size + 12
        if kind == b"tEXt" and data.split(b"\x00", 1)[0] in {b"chara", b"ccv3"}:
            label, encoded = data.split(b"\x00", 1)
            cards.append((label.decode(), json.loads(base64.b64decode(encoded).decode("utf-8"))))
    return cards


def _minimal_png(card: dict) -> bytes:
    """Build a parseable PNG with one SillyTavern chara metadata chunk."""
    return _png_with_metadata(("chara", card))


class RankPresentationCleanupTests(unittest.TestCase):
    def test_static_rank_badge_surface_is_removed(self):
        self.assertFalse(hasattr(quality, "rank_badge"))
        self.assertFalse(hasattr(quality, "RANK_BADGES"))


class ParseRankTests(unittest.TestCase):
    def test_single_letter(self):
        self.assertEqual(quality.parse_rank("S"), "S")

    def test_letter_with_justification(self):
        self.assertEqual(quality.parse_rank("A — the personality is sharp"), "A")

    def test_prefixed_label(self):
        self.assertEqual(quality.parse_rank("Rank: B"), "B")

    def test_garbage_returns_none(self):
        self.assertIsNone(quality.parse_rank(""))
        self.assertIsNone(quality.parse_rank("this is not a rank"))


class ParseOptimizedFieldsTests(unittest.TestCase):
    def test_bare_json(self):
        raw = '{"description": "new desc", "personality": "new persona", "bogus": 1}'
        result = quality.parse_optimized_fields(raw)
        self.assertEqual(result, {"description": "new desc", "personality": "new persona"})

    def test_fenced_json(self):
        raw = '```json\n{"description": "new"}\n```'
        self.assertEqual(quality.parse_optimized_fields(raw), {"description": "new"})

    def test_invalid_returns_none(self):
        self.assertIsNone(quality.parse_optimized_fields(""))
        self.assertIsNone(quality.parse_optimized_fields("no json here"))
        self.assertIsNone(quality.parse_optimized_fields('{"unrelated": "value"}'))


class MergeOptimizedFieldsTests(unittest.TestCase):
    def test_top_level_container(self):
        card = {"name": "Alice", "description": "old"}
        result = quality.merge_optimized_fields(card, {"description": "new"})
        self.assertEqual(result["description"], "new")
        self.assertEqual(card["description"], "old")
        self.assertEqual(card["name"], "Alice")

    def test_data_container(self):
        card = {"data": {"name": "Alice", "description": "old"}, "spec": "chara_card_v2"}
        result = quality.merge_optimized_fields(card, {"description": "new"})
        self.assertEqual(result["data"]["description"], "new")
        self.assertEqual(card["data"]["description"], "old")
        self.assertEqual(card["data"]["name"], "Alice")


class WritePngCharaBytesTests(unittest.TestCase):
    def test_roundtrip_preserves_other_chunks(self):
        card = {"name": "Alice", "description": "old", "personality": "p"}
        raw = _minimal_png(card)
        rewritten = quality.write_png_chara_bytes(raw, {**card, "description": "new"})
        parsed = parse_png_chara_bytes(rewritten)
        self.assertEqual(parsed["description"], "new")
        self.assertEqual(parsed["name"], "Alice")

    def test_updates_consistent_v2_and_v3_metadata_copies(self):
        v2 = {
            "spec": "chara_card_v2",
            "data": {"name": "Alice", "description": "old", "extensions": {"version": 2}},
        }
        v3 = {
            "spec": "chara_card_v3",
            "data": {"name": "Alice", "description": "old", "extensions": {"version": 3}},
        }
        raw = _png_with_metadata(("chara", v2), ("ccv3", v3))

        rewritten = quality.write_png_chara_bytes(raw, quality.merge_optimized_fields(v2, {"description": "new"}))
        cards = _metadata_cards(rewritten)

        self.assertEqual([label for label, _card in cards], ["chara", "ccv3"])
        self.assertEqual([card["data"]["description"] for _label, card in cards], ["new", "new"])
        self.assertEqual([card["data"]["extensions"]["version"] for _label, card in cards], [2, 3])
        self.assertEqual([card["spec"] for _label, card in cards], ["chara_card_v2", "chara_card_v3"])

    def test_updates_every_consistent_duplicate_chara_chunk(self):
        card = {"name": "Alice", "description": "old"}
        raw = _png_with_metadata(("chara", card), ("chara", card))

        rewritten = quality.write_png_chara_bytes(raw, {**card, "description": "new"})

        self.assertEqual(
            [item["description"] for _label, item in _metadata_cards(rewritten)],
            ["new", "new"],
        )

    def test_rejects_conflicting_metadata_copies(self):
        v2 = {"data": {"name": "Alice", "description": "old"}}
        v3 = {"data": {"name": "Alice", "description": "different"}}
        raw = _png_with_metadata(("chara", v2), ("ccv3", v3))

        with self.assertRaisesRegex(ValueError, "conflicting PNG character metadata"):
            quality.write_png_chara_bytes(raw, quality.merge_optimized_fields(v2, {"description": "new"}))

    def test_rejects_malformed_secondary_metadata(self):
        card = {"name": "Alice", "description": "old"}
        malformed = b"ccv3\x00" + base64.b64encode(b"not-json")
        malformed_chunk = (
            struct.pack(">I", len(malformed))
            + b"tEXt"
            + malformed
            + struct.pack(">I", zlib.crc32(b"tEXt" + malformed) & 0xFFFFFFFF)
        )
        raw = _minimal_png(card)
        raw = raw[:-12] + malformed_chunk + raw[-12:]

        with self.assertRaisesRegex(ValueError, "invalid PNG character metadata"):
            quality.write_png_chara_bytes(raw, {**card, "description": "new"})

    def test_preserves_non_character_chunks_byte_for_byte(self):
        card = {"name": "Alice", "description": "old"}
        ancillary_data = b"Comment\x00keep-this-byte-for-byte"
        ancillary = (
            struct.pack(">I", len(ancillary_data))
            + b"tEXt"
            + ancillary_data
            + struct.pack(">I", zlib.crc32(b"tEXt" + ancillary_data) & 0xFFFFFFFF)
        )
        raw = _minimal_png(card)
        raw = raw[:-12] + ancillary + raw[-12:]

        rewritten = quality.write_png_chara_bytes(raw, {**card, "description": "new"})

        self.assertIn(ancillary, rewritten)

    def test_validated_noop_preserves_every_input_byte(self):
        v2 = {"spec": "chara_card_v2", "data": {"name": "Alice", "description": "old"}}
        v3 = {"spec": "chara_card_v3", "data": {"name": "Alice", "description": "old"}}
        raw = _png_with_metadata(("chara", v2), ("ccv3", v3))

        self.assertEqual(quality.write_png_chara_bytes(raw, v2), raw)

    def test_rejects_ccv3_only_card_until_the_canonical_reader_supports_it(self):
        raw = _png_with_metadata(("ccv3", {"data": {"name": "Alice", "description": "old"}}))

        with self.assertRaisesRegex(ValueError, "PNG has no SillyTavern chara metadata"):
            quality.write_png_chara_bytes(raw, {"data": {"name": "Alice", "description": "new"}})

    def test_rejects_non_png(self):
        with self.assertRaises(ValueError):
            quality.write_png_chara_bytes(b"not a png", {})

    def test_rejects_missing_chara_chunk(self):
        fake = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 0) + b"IEND" + struct.pack(">I", 0)
        with self.assertRaises(ValueError):
            quality.write_png_chara_bytes(fake, {})


class CharacterQualityPersistenceTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app_settings_builder.db_file = Path(self.tmp.name) / "bridge.sqlite3"
        self.app_settings_builder.character_dir = Path(self.tmp.name) / "characters"
        self.app_settings = self.app_settings_builder.build()
        self.app_settings.character_dir.mkdir()
        for name in ("alice", "bob", "carol"):
            (self.app_settings.character_dir / f"{name}.png").write_bytes(_minimal_png({"name": name}))
        self.db = db_connect(app_settings=self.app_settings)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_store_and_load_rank(self):
        quality.store_character_rank(self.db, "alice.png", "S", app_settings=self.app_settings)
        self.assertEqual(quality.character_rank(self.db, "alice.png", app_settings=self.app_settings), "S")

    def test_invalid_tier_not_stored(self):
        quality.store_character_rank(self.db, "bob.png", "Z", app_settings=self.app_settings)
        self.assertEqual(quality.character_rank(self.db, "bob.png", app_settings=self.app_settings), "")

    def test_unranked_defaults_empty(self):
        self.assertEqual(quality.character_rank(self.db, "carol.png", app_settings=self.app_settings), "")


class CharacterQualityModelTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app_settings_builder.db_file = Path(self.tmp.name) / "bridge.sqlite3"
        self.app_settings_builder.character_dir = Path(self.tmp.name) / "characters"
        self.app_settings = self.app_settings_builder.build()
        self.app_settings.character_dir.mkdir()
        for name in ("alice", "bob", "carol"):
            (self.app_settings.character_dir / f"{name}.png").write_bytes(_minimal_png({"name": name}))
        self.db = db_connect(app_settings=self.app_settings)
        self.session = {"session_id": "s", "model_id": "m"}

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_rank_character_stores_parsed_tier(self):
        port = make_test_provider_port(generate_backend=lambda *a, **k: "S — distinctive and engaging")
        with mock.patch.object(quality, "task_model_for_session", return_value="test-model"):
            rank = quality.rank_character(
                self.db,
                "chat",
                self.session,
                {"name": "Alice", "description": "x"},
                "alice.png",
                provider_port=port,
                app_settings=self.app_settings,
            )
        self.assertEqual(rank, "S")
        self.assertEqual(quality.character_rank(self.db, "alice.png", app_settings=self.app_settings), "S")

    def test_rank_character_returns_none_on_provider_failure(self):
        def boom(*a, **k):
            raise RuntimeError("provider down")

        port = make_test_provider_port(generate_backend=boom)
        with mock.patch.object(quality, "task_model_for_session", return_value="test-model"):
            rank = quality.rank_character(
                self.db,
                "chat",
                self.session,
                {"name": "Alice"},
                "alice.png",
                provider_port=port,
                app_settings=self.app_settings,
            )
        self.assertIsNone(rank)
        self.assertEqual(quality.character_rank(self.db, "alice.png", app_settings=self.app_settings), "")

    def test_optimize_character_parses_json_reply(self):
        def backend(*a, **k):
            return json.dumps({"description": "new", "personality": "refined"})

        port = make_test_provider_port(generate_backend=backend)
        with mock.patch.object(quality, "task_model_for_session", return_value="test-model"):
            result = quality.optimize_character(
                self.db,
                "chat",
                self.session,
                {"name": "Alice", "description": "old"},
                provider_port=port,
                app_settings=self.app_settings,
            )
        self.assertEqual(result, {"description": "new", "personality": "refined"})

    def test_optimize_character_returns_none_on_invalid_output(self):
        port = make_test_provider_port(generate_backend=lambda *a, **k: "no json here")
        with mock.patch.object(quality, "task_model_for_session", return_value="test-model"):
            result = quality.optimize_character(
                self.db,
                "chat",
                self.session,
                {"name": "Alice"},
                provider_port=port,
                app_settings=self.app_settings,
            )
        self.assertIsNone(result)

    def test_optimize_character_returns_none_when_utility_model_resolution_fails(self):
        port = make_test_provider_port(generate_backend=mock.Mock(side_effect=ModelRoutingError("bad model selection")))
        with mock.patch.object(quality, "task_model_for_session", return_value="missing::model"):
            result = quality.optimize_character(
                self.db,
                "chat",
                self.session,
                {"name": "Alice"},
                provider_port=port,
                app_settings=self.app_settings,
            )

        self.assertIsNone(result)

    def test_optimize_character_raises_canonical_provider_failure(self):
        error = ProviderRequestError("rate_limit", "provider::model", status=429)
        port = make_test_provider_port(generate_backend=mock.Mock(side_effect=error))
        with (
            mock.patch.object(quality, "task_model_for_session", return_value="provider::model"),
            self.assertRaises(ProviderRequestError) as raised,
        ):
            quality.optimize_character(
                self.db,
                "chat",
                self.session,
                {"name": "Alice"},
                provider_port=port,
                app_settings=self.app_settings,
            )

        self.assertIs(raised.exception, error)


if __name__ == "__main__":
    unittest.main()


class OptimizerSuggestionPromptTests(unittest.TestCase):
    def test_optimizer_prompt_includes_manual_suggestion_as_bounded_guidance(self):
        prompt = quality.optimize_prompt(
            {"name": "Alice", "description": "old"},
            suggestion="Make her more sarcastic, preserve the backstory.",
        )
        joined = "\n".join(str(message["content"]) for message in prompt)
        self.assertIn("<user_suggestion>", joined)
        self.assertIn("Make her more sarcastic, preserve the backstory.", joined)
        self.assertIn("does not override", joined)
