from __future__ import annotations

import struct
import zlib
from pathlib import Path

from settings_test_support import make_test_settings

from bridge.limits import IMAGE_MAX_BYTES


def _chunk(kind: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(kind)
    crc = zlib.crc32(data, crc) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)


def _png(*extra_chunks: tuple[bytes, bytes]) -> bytes:
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    extras = b"".join(_chunk(kind, data) for kind, data in extra_chunks)
    idat = _chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
    return signature + ihdr + extras + idat + _chunk(b"IEND", b"")


def _settings(tmp_path: Path):
    character_dir = tmp_path / "characters"
    character_dir.mkdir()
    return make_test_settings(
        home=tmp_path,
        character_dir=character_dir,
        default_character_file="Default.png",
    )


def test_valid_native_character_png_loads_reference_without_reading_chara_metadata(tmp_path):
    from bridge.image_reference import load_character_reference

    settings = _settings(tmp_path)
    raw = _png()
    (settings.character_dir / "Mira.png").write_bytes(raw)

    reference = load_character_reference("Mira.png", app_settings=settings)

    assert reference is not None
    assert reference.data == raw
    assert reference.mime_type == "image/png"
    assert reference.filename == "Mira.png"


def test_valid_png_with_invalid_chara_metadata_is_still_a_reference(tmp_path):
    from bridge.image_reference import load_character_reference

    settings = _settings(tmp_path)
    raw = _png((b"tEXt", b"chara\x00not-valid-base64-or-json"))
    (settings.character_dir / "Mira.png").write_bytes(raw)

    reference = load_character_reference("Mira.png", app_settings=settings)

    assert reference is not None
    assert reference.data == raw


def test_traversal_character_name_returns_no_reference(tmp_path):
    from bridge.image_reference import load_character_reference

    settings = _settings(tmp_path)
    (tmp_path / "outside.png").write_bytes(_png())

    assert load_character_reference("../outside.png", app_settings=settings) is None


def test_missing_character_file_returns_no_reference(tmp_path):
    from bridge.image_reference import load_character_reference

    settings = _settings(tmp_path)

    assert load_character_reference("Missing.png", app_settings=settings) is None


def test_corrupt_or_truncated_png_returns_no_reference(tmp_path):
    from bridge.image_reference import load_character_reference

    settings = _settings(tmp_path)
    path = settings.character_dir / "Mira.png"
    path.write_bytes(_png()[:-5])

    assert load_character_reference("Mira.png", app_settings=settings) is None


def test_png_with_corrupt_chunk_crc_returns_no_reference(tmp_path):
    from bridge.image_reference import load_character_reference

    settings = _settings(tmp_path)
    raw = bytearray(_png())
    raw[29] ^= 0x01
    (settings.character_dir / "Mira.png").write_bytes(raw)

    assert load_character_reference("Mira.png", app_settings=settings) is None


def test_oversized_reference_returns_no_reference_without_persisting_copy(tmp_path):
    from bridge.image_reference import load_character_reference

    settings = _settings(tmp_path)
    source = settings.character_dir / "Mira.png"
    source.write_bytes(_png() + b"x" * IMAGE_MAX_BYTES)
    before = sorted(path.name for path in settings.character_dir.iterdir())

    assert load_character_reference("Mira.png", app_settings=settings) is None
    assert sorted(path.name for path in settings.character_dir.iterdir()) == before


def test_reference_loader_rechecks_file_at_generation_time_after_file_removed(tmp_path):
    from bridge.image_reference import load_character_reference

    settings = _settings(tmp_path)
    source = settings.character_dir / "Mira.png"
    source.write_bytes(_png())
    assert source.is_file()
    source.unlink()

    assert load_character_reference("Mira.png", app_settings=settings) is None
