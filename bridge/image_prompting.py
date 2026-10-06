"""Bound rendering and character-reference directives to image model prompt limits."""

from __future__ import annotations

from bridge.image_routing import ImageRoute, image_prompt_max_chars
from bridge.image_styles import IMAGE_DEFAULT_STYLE, image_style_prompt_prefix

_REFERENCE_PROMPT_PREFIX = (
    "Use the supplied image as the primary character's identity reference. "
    "Preserve facial identity, hairstyle, distinctive physical traits, apparent age, and established design. "
    "Follow the scene for pose, expression, clothing, environment, lighting, framing, and camera angle; "
    "do not merely recreate the portrait. Scene: "
)


def scene_prompt_max_chars(route: ImageRoute, *, style: str = IMAGE_DEFAULT_STYLE) -> int:
    available = image_prompt_max_chars(route) - len(image_style_prompt_prefix(style))
    if route.transport == "reference":
        available -= len(_REFERENCE_PROMPT_PREFIX)
    if available < 1:
        raise ValueError(f"Image prompt for {route.model} has no room for scene content")
    return available


def build_image_prompt(scene_prompt: str, *, route: ImageRoute, style: str = IMAGE_DEFAULT_STYLE) -> str:
    scene = " ".join(str(scene_prompt or "").split())
    if not scene or len(scene) > scene_prompt_max_chars(route, style=style):
        limit = image_prompt_max_chars(route)
        raise ValueError(
            f"Image prompt for {route.model} exceeds the {limit:,}-character limit "
            "after applying style and any character identity reference"
        )
    reference_prefix = _REFERENCE_PROMPT_PREFIX if route.transport == "reference" else ""
    return image_style_prompt_prefix(style) + reference_prefix + scene
