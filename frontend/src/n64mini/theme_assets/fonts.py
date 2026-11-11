"""Fonts bundled with the theme (``themes/n64-classic-mini/fonts``).

The art generator and the harness renderer both use the same TTFs the theme
ships, so dev renders match what Batocera draws, and no system font paths are needed.
"""

from __future__ import annotations

from enum import StrEnum
from functools import cache

from PIL import ImageFont

from n64mini.paths import THEME_FONTS_DIR


class Face(StrEnum):
    """Font files bundled in the theme's ``fonts/`` directory."""

    SANS = "DejaVuSans.ttf"
    SANS_BOLD = "DejaVuSans-Bold.ttf"
    SERIF_BOLD = "DejaVuSerif-Bold.ttf"


@cache
def font(face: Face, size: int) -> ImageFont.FreeTypeFont:
    """Load (and cache) a bundled font at ``size`` pixels."""
    return ImageFont.truetype(THEME_FONTS_DIR / face.value, max(8, size))
