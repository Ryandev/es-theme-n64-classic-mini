"""Bottom console bar overlay (AC-T21): ``art/console.png``.

Draws the bottom-left brand wordmark (``CLASSIC 64`` by default, or ``N64MINI_BRAND_TEXT`` /
``N64MINI_SVG_FRONT_BEZEL`` when configured in the environment) and the bottom-right crimson
Power LED indicator (horizontally aligned with the top-right 3D emblem). Like ``art/brand.png``,
it remains its own layer so ``frontend/run bundle`` can swap in ``local/console.png`` when present.
"""

from __future__ import annotations

from typing import Final

from PIL import Image, ImageDraw

from n64mini.paths import DEFAULT_BRAND_TEXT, SVG_ASSETS_DIR, brand_text, svg_asset
from n64mini.theme_assets.fonts import Face, font
from n64mini.theme_assets.shapes import (
    draw_power_led,
    extract_nintendo64_wordmark,
    render_svg,
)

CONSOLE_SIZE: Final = (1200, 82)
WORDMARK_POS: Final = (16, 13)
POWER_LED_X: Final = 1118


def _render_text_wordmark(label: str) -> Image.Image:
    """Render an arbitrary brand string into the bezel wordmark box."""
    canvas = Image.new("RGBA", (220, 82), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    parts = label.rsplit(" ", 1)
    main_txt = parts[0]
    sup_txt = parts[1] if len(parts) > 1 else ""
    main_face = font(Face.SANS_BOLD, 20)
    draw.text((34, 30), main_txt, font=main_face, fill=(228, 232, 242, 255))
    if sup_txt:
        mw = int(draw.textlength(main_txt, font=main_face))
        sup_face = font(Face.SANS_BOLD, 14)
        draw.text((34 + mw + 2, 24), sup_txt, font=sup_face, fill=(255, 45, 30, 255))
    return canvas


def build_console() -> Image.Image:
    """Bottom-left brand wordmark and bottom-right crimson Power LED indicator."""
    _, h = CONSOLE_SIZE
    img = Image.new("RGBA", CONSOLE_SIZE, (0, 0, 0, 0))
    bezel_path = svg_asset("front_bezel.svg")
    label = brand_text()
    if label != DEFAULT_BRAND_TEXT and bezel_path == SVG_ASSETS_DIR / "front_bezel.svg":
        bezel_svg = _render_text_wordmark(label)
    else:
        bezel_svg = render_svg(
            bezel_path,
            (220, 82),
            color_overrides={
                "#14161c": "none",
                "#484c58": "none",
                "#0a0b0e": "none",
                "#181b22": "none",
            },
        )
    wordmark = extract_nintendo64_wordmark(bezel_svg)
    bx, by = WORDMARK_POS
    ww, wh = wordmark.size
    img.alpha_composite(wordmark, (bx, by))
    draw = ImageDraw.Draw(img, "RGBA")
    gx0, gy0, gx1, gy1 = bx + 4, by + wh + 1, bx + ww - 6, by + wh + 6
    draw.rounded_rectangle(
        (gx0, gy0, gx1, gy1),
        radius=2,
        fill=(30, 33, 38, 225),
        outline=(98, 104, 114, 165),
    )
    draw_power_led(img, POWER_LED_X, h // 2, w=52, h=16)
    return img
