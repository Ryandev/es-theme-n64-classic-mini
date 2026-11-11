"""The 1280x720 static background plate: SGI horizon, console bars, title plaque.

Dynamic content (game title, box art, tiles) is drawn by Batocera at runtime and
must not be baked in here.
"""

from __future__ import annotations

from typing import Final

from PIL import Image, ImageDraw

from n64mini.theme_assets.fonts import Face, font
from n64mini.theme_assets.shapes import draw_led_rounded_border
from n64mini.theme_assets.texture import Gradient, Tex, make_n64_surface_tex

W: Final = 1280
H: Final = 720
TOP_H: Final = 92  # 12.78% of 720p, matching the Classic Mini reference top bar
BOTTOM_Y: Final = 630  # 90 px (12.50% of 720p), the lower console bar (AC-T21)


def _sgi_horizon(draw: ImageDraw.ImageDraw) -> None:
    horizon_y = int(H * 0.52)
    mountain = [
        (0, 0),
        (90, -38),
        (195, -12),
        (310, -54),
        (440, -18),
        (580, -46),
        (720, -16),
        (860, -52),
        (990, -20),
        (1120, -44),
        (1280, -14),
        (1280, 0),
    ]
    pts = [(x, horizon_y + dy) for x, dy in mountain]
    for i in range(len(pts) - 2):
        a, b = pts[i], pts[i + 1]
        fill = (34, 56, 118) if i % 2 == 0 else (24, 40, 88)
        draw.polygon([a, b, (b[0], horizon_y), (a[0], horizon_y)], fill=fill, outline=(52, 85, 168))

    draw.rectangle((0, horizon_y - 3, W, horizon_y + 3), fill=(58, 98, 192))
    for ix in range(-7, 8):
        x_bot = W // 2 + ix * 160
        x_top = W // 2 + int(ix * 34)
        draw.line([(x_top, horizon_y), (x_bot, H)], fill=(78, 142, 242), width=2)
        draw.line([(x_top, horizon_y), (x_bot, 0)], fill=(46, 84, 158), width=1)
    for iy in range(1, 7):
        yy = horizon_y + int((iy / 6.0) ** 1.7 * (H - horizon_y))
        draw.line([(0, yy), (W, yy)], fill=(84, 148, 245), width=2)


def _molded_bar(
    canvas: Image.Image,
    y0: int,
    h: int,
    *,
    is_top: bool,
    recess_box: tuple[int, int, int, int] | None = None,
) -> None:
    """Draw a clean molded N64 charcoal bar with smooth vertical gradient and bevel seams."""
    top_rgb = (74, 78, 86)
    bot_rgb = (46, 49, 55)
    draw = ImageDraw.Draw(canvas)
    for row in range(h):
        t = row / max(h - 1, 1)
        r = int(top_rgb[0] * (1 - t) + bot_rgb[0] * t)
        g = int(top_rgb[1] * (1 - t) + bot_rgb[1] * t)
        b = int(top_rgb[2] * (1 - t) + bot_rgb[2] * t)
        draw.line([(0, y0 + row), (W - 1, y0 + row)], fill=(r, g, b))
    if recess_box is not None:
        rx0, ry0, rx1, ry1 = recess_box
        draw.rounded_rectangle((rx0, ry0, rx1, ry1), radius=6, fill=(36, 39, 44))
        draw.line([(rx0 + 2, ry0 + 1), (rx1 - 2, ry0 + 1)], fill=(18, 20, 24), width=2)
        draw.line([(rx0 + 2, ry1 - 1), (rx1 - 2, ry1 - 1)], fill=(96, 102, 112), width=2)
        draw.line([(rx0 + 1, ry0 + 2), (rx0 + 1, ry1 - 2)], fill=(22, 24, 28), width=2)
        draw.line([(rx1 - 1, ry0 + 2), (rx1 - 1, ry1 - 2)], fill=(90, 96, 106), width=2)
    if is_top:
        draw.line([(0, y0), (W - 1, y0)], fill=(122, 128, 138), width=2)
        draw.line([(0, y0 + 2), (W - 1, y0 + 2)], fill=(96, 102, 112), width=1)
        draw.line([(0, y0 + h - 3), (W - 1, y0 + h - 3)], fill=(30, 32, 37), width=2)
        draw.line([(0, y0 + h - 1), (W - 1, y0 + h - 1)], fill=(14, 15, 18), width=1)
    else:
        draw.line([(0, y0), (W - 1, y0)], fill=(18, 20, 24), width=1)
        draw.line([(0, y0 + 1), (W - 1, y0 + 2)], fill=(126, 132, 142), width=2)
        draw.line([(0, y0 + 3), (W - 1, y0 + 3)], fill=(96, 102, 112), width=1)
        draw.line([(0, y0 + h - 2), (W - 1, y0 + h - 1)], fill=(24, 26, 30), width=2)
    for sx in (38, W - 38):
        draw.line([(sx - 1, y0 + 2), (sx - 1, y0 + h - 2)], fill=(24, 26, 30), width=2)
        draw.line([(sx + 1, y0 + 2), (sx + 1, y0 + h - 2)], fill=(108, 114, 124), width=1)


def _top_bar(canvas: Image.Image) -> None:
    """Molded charcoal N64 console front bar with recessed centre pocket for ``art/brand.png``."""
    _molded_bar(canvas, 0, TOP_H, is_top=True, recess_box=(470, 10, 810, 82))


def _title_plaque(canvas: Image.Image) -> None:
    """Dark console pill plaque with LED-highlighted off-white border; ``md_name`` draws over it."""
    draw_led_rounded_border(
        canvas,
        (236, 103, 1044, 160),
        ((176, 186, 200), (255, 255, 255), (20, 24, 32), (26, 30, 38)),
        geom=(5, 14),
        shadow=True,
    )


def _dpad_icon(canvas: Image.Image, cx: int, cy: int, highlight_dir: str) -> None:
    draw = ImageDraw.Draw(canvas)
    arm_w, arm_l = 4, 11
    base_col = (118, 126, 138)
    hi_col = (245, 248, 255)
    rim_col = (18, 20, 26)
    draw.rectangle((cx - arm_w - 1, cy - arm_l - 1, cx + arm_w + 1, cy + arm_l + 1), fill=rim_col)
    draw.rectangle((cx - arm_l - 1, cy - arm_w - 1, cx + arm_l + 1, cy + arm_w + 1), fill=rim_col)
    draw.rectangle((cx - arm_w, cy - arm_l, cx + arm_w, cy + arm_l), fill=base_col)
    draw.rectangle((cx - arm_l, cy - arm_w, cx + arm_l, cy + arm_w), fill=base_col)
    if highlight_dir == "up":
        draw.rectangle((cx - arm_w, cy - arm_l, cx + arm_w, cy - 2), fill=hi_col)
    else:
        draw.rectangle((cx - arm_w, cy + 2, cx + arm_w, cy + arm_l), fill=hi_col)


def _a_start_badge(canvas: Image.Image, cx: int, cy: int) -> None:
    draw = ImageDraw.Draw(canvas)
    draw.ellipse(
        (cx - 11, cy - 11, cx + 11, cy + 11),
        fill=(46, 118, 232),
        outline=(18, 22, 30),
        width=1,
    )
    draw.ellipse((cx - 8, cy - 9, cx + 8, cy - 3), fill=(130, 185, 255))
    f_btn = font(Face.SANS_BOLD, 12)
    tw = int(draw.textlength("A", font=f_btn))
    draw.text((cx - tw // 2, cy - 8), "A", font=f_btn, fill=(250, 252, 255))


def _button_prompts(canvas: Image.Image) -> None:
    """Three Classic Mini button prompts: Up=Menu, Down=Save States, A=Start Game."""
    draw = ImageDraw.Draw(canvas)
    f_prompt = font(Face.SANS, 16)
    p_cy = 591
    for kind, label, x_start in (
        ("up", "Menu", 435),
        ("down", "Save States", 585),
        ("a", "Start Game", 790),
    ):
        if kind in ("up", "down"):
            _dpad_icon(canvas, x_start, p_cy, highlight_dir=kind)
        else:
            _a_start_badge(canvas, x_start, p_cy)
        draw.text((x_start + 19, p_cy - 9), label, font=f_prompt, fill=(10, 12, 18))
        draw.text((x_start + 18, p_cy - 10), label, font=f_prompt, fill=(240, 244, 252))


def _bottom_bar(canvas: Image.Image) -> None:
    """Lower front console bar with the same molded charcoal finish as the top bar (AC-T21)."""
    _molded_bar(canvas, BOTTOM_Y, H - BOTTOM_Y, is_top=False)


def build_background() -> Image.Image:
    """Render the full 1280x720 background plate."""
    sky = Gradient.vertical((12, 18, 44), (42, 70, 142))
    canvas = make_n64_surface_tex(W, H, sky, Tex(32, 20, 9))
    _sgi_horizon(ImageDraw.Draw(canvas))
    _top_bar(canvas)
    _title_plaque(canvas)
    _button_prompts(canvas)
    _bottom_bar(canvas)
    return canvas
