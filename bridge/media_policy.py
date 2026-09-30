"""Classify documents that can enter conversational image processing before download."""

from pathlib import Path


def conversational_document(document: dict) -> bool:
    # PNGs can be cards or images; ownership must be checked before discovering which.
    return Path(str(document.get("file_name") or "")).suffix.casefold() == ".png" or str(
        document.get("mime_type") or ""
    ).startswith("image/")
