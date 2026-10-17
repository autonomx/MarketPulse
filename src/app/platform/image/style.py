"""Prompt directives for image-generation style selections."""
from __future__ import annotations

from typing import Any


_STYLE_DIRECTIVES = {
    "photorealistic": (
        "Render as a photorealistic photograph with natural lighting, lifelike materials, "
        "realistic anatomy, and camera-like detail."
    ),
    "cinematic": (
        "Render as a cinematic film still with dramatic lighting, deliberate composition, "
        "depth of field, and film color grading."
    ),
    "concept art": (
        "Render as professional concept art with painterly brushwork, strong shape language, "
        "atmospheric design, and production-ready visual development."
    ),
    "digital illustration": (
        "Render as a polished digital illustration with clean forms, deliberate linework, "
        "rich color, and detailed painted rendering."
    ),
    "watercolor": (
        "Render as a traditional watercolor painting with translucent washes, visible paper "
        "texture, soft pigment edges, and organic color variation."
    ),
    "anime": (
        "Render as a Japanese anime illustration, not a photograph, with expressive line art, "
        "cel shading, stylized forms, and vibrant color."
    ),
}


def normalize_image_style(value: Any) -> str:
    """Normalize UI and API style labels to their canonical form."""

    normalized = " ".join(str(value or "").strip().lower().replace("_", " ").replace("-", " ").split())
    aliases = {
        "photo realistic": "photorealistic",
        "conceptart": "concept art",
        "digital art": "digital illustration",
        "illustration": "digital illustration",
        "water colour": "watercolor",
        "japanese anime": "anime",
    }
    return aliases.get(normalized, normalized)


def apply_image_style(prompt: str, style: Any) -> str:
    """Return the provider prompt with an explicit visual-style directive."""

    subject = " ".join(str(prompt or "").split())
    normalized_style = normalize_image_style(style)
    if not subject or not normalized_style:
        return subject

    directive = _STYLE_DIRECTIVES.get(normalized_style)
    if directive is None:
        directive = f"Render in a {normalized_style} visual style."
    return f"{directive}\nSubject and scene: {subject}"
