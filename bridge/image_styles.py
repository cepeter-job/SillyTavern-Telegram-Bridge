"""Session-scoped rendering styles shared by image panels and prompt construction."""

from __future__ import annotations

from bridge.metadata import get_meta, set_meta

IMAGE_DEFAULT_STYLE = "realism"
IMAGE_STYLE_OPTIONS = (("realism", "Realism"), ("anime", "Anime"), ("manhwa", "Manhwa"))
_STYLE_PROMPT_PREFIXES = {
    "realism": "Style: Realism, photorealistic detail, natural textures; override source medium. ",
    "anime": (
        "Style: Anime, semi-realistic 2D art, clean linework, soft gradient cel shading, "
        "luminous eyes, glossy hair, smooth skin, vivid colors; override source medium. "
    ),
    # Leave scene room beside the identity directive on 512-character reference models.
    "manhwa": (
        "Style: Manhwa, Korean webtoon, crisp linework, rich cel shading, polished detail, "
        "balanced lighting; override source medium. "
    ),
}


def _image_style_key(chat_id: str, session_id: str) -> str:
    return f"image_style:{chat_id}:{session_id}"


def session_image_style(db, chat_id: str, session_id: str) -> str:
    value = get_meta(db, _image_style_key(chat_id, session_id), "").strip()
    return value if value in _STYLE_PROMPT_PREFIXES else IMAGE_DEFAULT_STYLE


def set_session_image_style(db, chat_id: str, session_id: str, style: str) -> str:
    value = str(style or "").strip()
    if value not in _STYLE_PROMPT_PREFIXES:
        raise ValueError("Unsupported image style")
    set_meta(db, _image_style_key(chat_id, session_id), value)
    return value


def reset_session_image_style(db, chat_id: str, session_id: str) -> None:
    set_meta(db, _image_style_key(chat_id, session_id), "")


def image_style_prompt_prefix(style: str) -> str:
    try:
        return _STYLE_PROMPT_PREFIXES[style]
    except KeyError:
        raise ValueError("Unsupported image style") from None
