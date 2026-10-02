"""Safe in-memory loading of native SillyTavern character PNG references."""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

from bridge.card_content import safe_character_path
from bridge.limits import IMAGE_MAX_BYTES
from bridge.settings import AppSettings

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class ImageReference:
    data: bytes
    mime_type: str
    filename: str


def _valid_png(raw: bytes) -> bool:
    if not raw.startswith(_PNG_SIGNATURE):
        return False
    pos = len(_PNG_SIGNATURE)
    chunk_index = 0
    saw_ihdr = False
    saw_idat = False
    saw_iend = False

    while pos < len(raw):
        if pos + 12 > len(raw):
            return False
        size = struct.unpack(">I", raw[pos : pos + 4])[0]
        chunk_type = raw[pos + 4 : pos + 8]
        data_start = pos + 8
        data_end = data_start + size
        crc_end = data_end + 4
        if data_end < data_start or crc_end > len(raw):
            return False

        data = raw[data_start:data_end]
        expected_crc = struct.unpack(">I", raw[data_end:crc_end])[0]
        actual_crc = zlib.crc32(chunk_type)
        actual_crc = zlib.crc32(data, actual_crc) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            return False

        if chunk_index == 0:
            if chunk_type != b"IHDR" or size != 13:
                return False
            width, height, _depth, _color, compression, filtering, interlace = struct.unpack(">IIBBBBB", data)
            if width <= 0 or height <= 0 or compression != 0 or filtering != 0 or interlace not in {0, 1}:
                return False
            saw_ihdr = True
        elif chunk_type == b"IHDR":
            return False

        if chunk_type == b"IDAT":
            if not saw_ihdr or saw_iend:
                return False
            saw_idat = True
        elif chunk_type == b"IEND":
            if size != 0 or not saw_idat:
                return False
            saw_iend = True
            pos = crc_end
            break

        pos = crc_end
        chunk_index += 1

    return saw_ihdr and saw_idat and saw_iend and pos == len(raw)


def load_character_reference(
    character_file: str,
    *,
    app_settings: AppSettings,
) -> ImageReference | None:
    path = safe_character_path(character_file, app_settings=app_settings)
    if path is None:
        return None
    try:
        with path.open("rb") as handle:
            raw = handle.read(IMAGE_MAX_BYTES + 1)
    except OSError:
        return None
    if not raw or len(raw) > IMAGE_MAX_BYTES or not _valid_png(raw):
        return None
    return ImageReference(data=raw, mime_type="image/png", filename=path.name)
