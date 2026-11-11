"""Carousel card frames, drawn as nine-patch textures.

The engine draws a grid tile's ``backgroundImage`` as a nine-patch behind the game's
image (``GridTileComponent.cpp``; ``NinePatchComponent.cpp`` maps each corner's
``cornerSize`` texels 1:1 and stretches the rest). So a frame is a small texture whose
corners hold the chamfer and bevel, and whose edges are uniform along their length.
The game's box art is drawn inset by the tile ``padding``, over the frame's recess.

The theme XML must use the same numbers: ``backgroundCornerSize`` = ``CORNER`` and the
gridtile ``padding`` = ``ART_INSET`` pixels. ``tests/test_n64_theme.py`` checks that.
"""

from __future__ import annotations

import math
from typing import Final

from PIL import Image, ImageDraw, ImageFilter

from n64mini.paths import SVG_ASSETS_DIR, svg_asset
from n64mini.theme_assets.fonts import Face, font
from n64mini.theme_assets.shapes import (
    draw_chiseled_box,
    draw_led_rounded_border,
    make_icon_cog,
    make_icon_library,
    make_icon_music,
    render_svg,
)
from n64mini.theme_assets.texture import Gradient, Tex

FRAME_SIZE: Final = 96
CORNER: Final = 24  # nine-patch corner, texels (= screen pixels)
ART_INSET: Final = 14  # where the box art starts, pixels from the tile edge

_CHARCOAL: Final = Gradient((92, 98, 116), (70, 76, 92), (44, 48, 60), (30, 33, 42))
_GOLD: Final = Gradient((255, 226, 92), (238, 196, 52), (196, 142, 18), (150, 104, 10))
_TEX: Final = Tex(8, 8, 10)


def build_tile_frame(*, selected: bool) -> Image.Image:
    """Rounded card frame with LED-highlighted border (off-white selected, grey unselected)."""
    img = Image.new("RGBA", (FRAME_SIZE, FRAME_SIZE), (0, 0, 0, 0))
    last = FRAME_SIZE - 1
    base_rgb = (176, 186, 200) if selected else (82, 88, 100)
    hi_rgb = (255, 255, 255) if selected else (126, 134, 148)
    rim_rgb = (20, 24, 32) if selected else (18, 20, 26)
    draw_led_rounded_border(
        img,
        (0, 0, last, last),
        (base_rgb, hi_rgb, rim_rgb, (18, 21, 28)),
        geom=(6, 16),
        shadow=False,
    )
    return img


BADGE_SIZE: Final = (64, 20)


def build_flip_badge(*, port: bool) -> Image.Image:
    """The pill on the card's bottom row saying whether Port mode is active (AC-T15).

    Styled like the suspend-point circles: outline + "PORT" text when available in Emulator mode,
    and filled gold + dark "PORT" text when flipped to Port mode.
    """
    img = Image.new("RGBA", BADGE_SIZE, (0, 0, 0, 0))
    w, h = BADGE_SIZE
    draw = ImageDraw.Draw(img)
    if port:
        draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=9, fill=(150, 104, 10))
        draw.rounded_rectangle((1, 1, w - 3, h - 3), radius=8, fill=(255, 220, 56))
        draw.rounded_rectangle((5, 2, w - 7, 5), radius=2, fill=(255, 244, 190))
        colour = (16, 18, 26)
    else:
        draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=9, fill=(110, 118, 136))
        draw.rounded_rectangle((2, 2, w - 3, h - 3), radius=7, fill=(16, 18, 26))
        colour = (110, 118, 136)
    face = font(Face.SANS_BOLD, 11)
    bbox = draw.textbbox((0, 0), "PORT", font=face)
    tx = (w - (bbox[2] - bbox[0])) / 2 - bbox[0]
    ty = (h - (bbox[3] - bbox[1])) / 2 - bbox[1]
    draw.text((tx, ty), "PORT", font=face, fill=colour)
    return img


LIBRARY_CARD_SIZE: Final = (320, 224)  # landscape, like an N64 box front
_LIBRARY_TEXT: Final = {
    "full": ("FULL", "LIBRARY", "EVERY GAME"),
    "showcase": ("THE", "SHOWCASE", "THE CLASSICS"),
}


def build_library_card(target: str) -> Image.Image:
    """The art for the Showcase/Full card (AC-T14): where launching it takes you.

    ``target`` is ``"full"`` (shown in the Showcase) or ``"showcase"`` (shown in Full). Original
    lettering on the theme's charcoal plate, so it reads as part of the console, not a game.
    """
    small, big, sub = _LIBRARY_TEXT[target]
    w, h = LIBRARY_CARD_SIZE
    img = Image.new("RGBA", LIBRARY_CARD_SIZE, (20, 22, 30, 255))
    draw_chiseled_box(
        img, (0, 0, w - 1, h - 1), chamf=16, bevel=8, colors=_CHARCOAL, tex=_TEX, shadow=False
    )
    draw_chiseled_box(
        img, (22, 64, w - 23, 150), chamf=10, bevel=5, colors=_GOLD, tex=_TEX, shadow=False
    )
    draw = ImageDraw.Draw(img)
    plate_inner = w - 2 * 22 - 2 * 16  # the gold plate, less its chamfers
    size = 44
    while draw.textlength(big, font=font(Face.SERIF_BOLD, size)) > plate_inner:
        size -= 1
    for text, face, top, colour in (
        (small, font(Face.SANS_BOLD, 22), 30, (240, 242, 248)),
        (big, font(Face.SERIF_BOLD, size), 107 - size * 0.6, (24, 20, 8)),
        (sub, font(Face.SANS_BOLD, 20), 170, (240, 242, 248)),
    ):
        draw.text(((w - draw.textlength(text, font=face)) / 2, top), text, font=face, fill=colour)
    return img


SYSTEM_LOGO_SIZE: Final = (480, 220)


def build_system_logo() -> Image.Image:
    """The N64 system's logo for the system view (AC-T10)."""
    w, h = SYSTEM_LOGO_SIZE
    img = Image.new("RGBA", SYSTEM_LOGO_SIZE, (0, 0, 0, 0))
    draw_chiseled_box(
        img, (0, 0, w - 1, h - 1), chamf=18, bevel=9, colors=_CHARCOAL, tex=_TEX, shadow=False
    )
    bezel = render_svg(svg_asset("front_bezel.svg"), (320, 120))
    img.paste(bezel, ((w - 320) // 2, 24), bezel)
    nintendo = render_svg(svg_asset("nintendo_logo.svg"), (140, 140))
    img.paste(nintendo, ((w - 140) // 2, h - 108), nintendo)
    return img


BRAND_SIZE: Final = (1200, 82)  # header bar overlay (x 40..1240, y 5..87)
ICON_X: Final = (505, 600, 695)  # 3 LED-style option icons inside the centre recessed pocket
N_LOGO_SIZE: Final = (68, 68)  # 3D cube emblem on the right side of the header bar
N_LOGO_CENTER_X: Final = 1118  # aligned horizontally with the bottom-right red Power LED


def build_brand() -> Image.Image:
    """Header bar overlay (``art/brand.png``): centre LED option icons and right 3D cube emblem.

    Draws the three flat LED-highlighted option icons (Settings cog, Showcase/Full library toggle,
    BG music toggle) centred in the top bar's recessed pocket and the 3D cube emblem on the right.
    The emblem is ``build_cube_emblem`` unless the owner points ``n_logo.svg`` at their own SVG
    (``N64MINI_SVG_N_LOGO`` or ``N64MINI_SVG_DIR``); ``frontend/run bundle`` can still override
    the whole layer with ``local/brand.png``.
    """
    _, h = BRAND_SIZE
    cy = h // 2
    img = Image.new("RGBA", BRAND_SIZE, (0, 0, 0, 0))
    icons = (make_icon_cog(), make_icon_library(), make_icon_music())
    for bx, icon in zip(ICON_X, icons, strict=True):
        iw, ih = icon.size
        img.alpha_composite(icon, (bx - iw // 2, cy - ih // 2))
    own_logo = svg_asset("n_logo.svg")
    n_logo = (
        build_cube_emblem()
        if own_logo == SVG_ASSETS_DIR / "n_logo.svg"
        else render_svg(own_logo, N_LOGO_SIZE)
    )
    n_logo = n_logo.crop(n_logo.getbbox())
    nw, nh = n_logo.size
    img.alpha_composite(n_logo, (N_LOGO_CENTER_X - nw // 2, cy - nh // 2))
    return img


type _Pt = tuple[float, float]

_EMBLEM_SS: Final = 8  # supersampling for the cube's edges and lettering
# Original emblem (AC-T21, DEC-5): a plain isometric cube, one colour per face, "64" on each.
EMBLEM_FACES: Final = {
    "top": (240, 72, 58),
    "left": (20, 150, 70),
    "right": (30, 70, 200),
}
_EMBLEM_EDGE: Final = (24, 14, 14)
_EMBLEM_INK: Final = (255, 244, 220)


def _face_text(size: int, origin: _Pt, u: _Pt, v: _Pt, fill: tuple[int, int, int]) -> Image.Image:
    """``64`` drawn on the parallelogram ``origin + s*u + t*v`` (s, t in 0..1) of a canvas."""
    side = 256
    flat = Image.new("L", (side, side), 0)
    ImageDraw.Draw(flat).text(
        (side / 2, side / 2), "64", font=font(Face.SANS_BOLD, side * 9 // 20), anchor="mm", fill=255
    )
    det = u[0] * v[1] - u[1] * v[0]
    # PIL's AFFINE maps each output pixel back to the flat square: invert [u v].
    a, b = v[1] / det * side, -v[0] / det * side
    d, e = -u[1] / det * side, u[0] / det * side
    c = -(a * origin[0] + b * origin[1])
    f = -(d * origin[0] + e * origin[1])
    mask = flat.transform(
        (size, size), Image.Transform.AFFINE, (a, b, c, d, e, f), Image.Resampling.BICUBIC
    )
    layer = Image.new("RGBA", (size, size), (*fill, 255))
    layer.putalpha(mask)
    return layer


def build_cube_emblem(size: int = N_LOGO_SIZE[0]) -> Image.Image:
    """The header bar's 3D emblem: an isometric cube with ``64`` on each visible face."""
    w = size * _EMBLEM_SS
    img = Image.new("RGBA", (w, w), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    r = w * 0.44
    k = math.cos(math.radians(30)) * r
    cx, cy = w / 2, w / 2 + w * 0.02
    top, top_r, bot_r = (cx, cy - r), (cx + k, cy - r / 2), (cx + k, cy + r / 2)
    bot, bot_l, top_l, mid = (cx, cy + r), (cx - k, cy + r / 2), (cx - k, cy - r / 2), (cx, cy)
    faces: dict[str, tuple[list[_Pt], _Pt, _Pt, _Pt]] = {
        # polygon, text origin, text x axis, text y axis
        "top": ([top, top_r, mid, top_l], top_l, _sub(top, top_l), _sub(mid, top_l)),
        "left": ([top_l, mid, bot, bot_l], top_l, _sub(mid, top_l), _sub(bot_l, top_l)),
        "right": ([mid, top_r, bot_r, bot], mid, _sub(top_r, mid), _sub(bot, mid)),
    }
    edge = round(w * 0.03)
    for name, (poly, origin, u, v) in faces.items():
        draw.polygon(poly, fill=EMBLEM_FACES[name])
        shadow = _face_text(w, (origin[0] + edge / 2, origin[1] + edge / 2), u, v, _EMBLEM_EDGE)
        img.alpha_composite(shadow)
        img.alpha_composite(_face_text(w, origin, u, v, _EMBLEM_INK))
    for poly, *_ in faces.values():
        draw.line([*poly, poly[0]], fill=_EMBLEM_EDGE, width=edge, joint="curve")
    # LED-style highlight along the top-left edge, like the option icons.
    draw.line(
        (_lerp(top_l, top, 0.25, edge * 1.5), _lerp(top_l, top, 0.7, edge * 1.5)),
        fill=(255, 255, 255, 170),
        width=edge,
    )
    return img.resize((size, size), Image.Resampling.LANCZOS)


def _sub(p: _Pt, q: _Pt) -> _Pt:
    return (p[0] - q[0], p[1] - q[1])


def _lerp(p: _Pt, q: _Pt, t: float, dy: float) -> _Pt:
    return (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t + dy)


LAUNCH_SPLASH_SIZE: Final = (640, 360)


def build_launch_splash() -> Image.Image:
    """The repo's game-launch splash frame (AC-T17, DEC-5): an original '64' emblem on black.

    No trademarks: the owner's console can carry the captured emulator boot frame
    (``local/launch_splash.png``, swapped in by ``frontend/run bundle``), never committed.
    """
    w, h = LAUNCH_SPLASH_SIZE
    img = Image.new("RGBA", LAUNCH_SPLASH_SIZE, (0, 0, 0, 255))
    box = 120
    x0 = (w - box) // 2
    y0 = (h - box) // 2
    draw_chiseled_box(
        img,
        (x0, y0, x0 + box - 1, y0 + box - 1),
        chamf=18,
        bevel=8,
        colors=_GOLD,
        tex=_TEX,
        shadow=False,
    )
    draw = ImageDraw.Draw(img)
    emblem = font(Face.SANS_BOLD, 56)
    bbox = draw.textbbox((0, 0), "64", font=emblem)
    tx = (w - (bbox[2] - bbox[0])) / 2 - bbox[0]
    ty = (h - (bbox[3] - bbox[1])) / 2 - bbox[1]
    draw.text((tx, ty), "64", font=emblem, fill=(24, 20, 8))
    return img


SAVE_DOT_SIZE: Final = (16, 16)


def build_save_dot(*, filled: bool) -> Image.Image:
    """One of a card's four suspend-point circles (AC-T09): a gold disc, or an empty ring.

    Each circle is its own image, shown when its slot's save state exists (the template binds
    ``visible`` to ``exists(...)``), so the engine needs no count.
    """
    img = Image.new("RGBA", SAVE_DOT_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    last = SAVE_DOT_SIZE[0] - 1
    if filled:
        draw.ellipse((0, 0, last, last), fill=(150, 104, 10))
        draw.ellipse((1, 1, last - 2, last - 2), fill=(255, 220, 56))
        draw.ellipse((4, 3, 8, 7), fill=(255, 244, 190))  # the highlight, light from top-left
    else:
        draw.ellipse((0, 0, last, last), fill=(110, 118, 136))
        draw.ellipse((2, 2, last - 2, last - 2), fill=(16, 18, 26))
    return img


BUTTON_ICON_SIZE: Final = (32, 32)
BUTTON_ICONS: Final = (
    "a",
    "b",
    "x",
    "y",
    "start",
    "select",
    "l",
    "r",
    "dpad_all",
    "dpad_ud",
    "dpad_lr",
    "c_up",
    "c_down",
    "c_left",
    "c_right",
)
type _RGB = tuple[int, int, int]
_ROUND_BUTTONS: Final[dict[str, tuple[_RGB, _RGB, _RGB, str, _RGB]]] = {
    "a": ((18, 42, 112), (36, 92, 224), (110, 168, 255), "A", (248, 250, 255)),
    "b": ((12, 82, 38), (24, 168, 76), (96, 224, 138), "B", (248, 250, 255)),
    "start": ((116, 16, 22), (220, 36, 44), (255, 112, 118), "S", (248, 250, 255)),
    "x": ((142, 98, 8), (245, 198, 34), (255, 236, 128), "X", (24, 20, 8)),
    "y": ((142, 98, 8), (245, 198, 34), (255, 236, 128), "Y", (24, 20, 8)),
}
_SHOULDER_BUTTONS: Final[dict[str, str]] = {"l": "L", "r": "R", "select": "Z"}
_C_ARROWS: Final[
    dict[str, tuple[tuple[float, float], tuple[float, float], tuple[float, float]]]
] = {
    "c_up": ((15.5, 10.2), (9.8, 20.1), (21.2, 20.1)),
    "c_down": ((15.5, 22.2), (9.8, 12.3), (21.2, 12.3)),
    "c_left": ((9.5, 16.2), (19.4, 10.5), (19.4, 21.9)),
    "c_right": ((21.5, 16.2), (11.6, 10.5), (11.6, 21.9)),
}


def _build_c_button_icon(kind: str) -> Image.Image:
    """Render a 4x-supersampled glossy yellow N64 C-arrow button matching ``btn_a``/``btn_b``."""
    scale = 4
    w, h = BUTTON_ICON_SIZE
    img = Image.new("RGBA", (w * scale, h * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((1 * scale, 1 * scale, (w - 2) * scale, (h - 2) * scale), fill=(112, 72, 4, 255))
    draw.ellipse((2 * scale, 2 * scale, (w - 3) * scale, (h - 3) * scale), fill=(204, 146, 10, 255))
    draw.ellipse((2 * scale, 2 * scale, (w - 4) * scale, (h - 5) * scale), fill=(232, 176, 20, 255))
    draw.ellipse((5 * scale, 4 * scale, 16 * scale, 12 * scale), fill=(255, 250, 210, 255))
    pts = [(px * scale, py * scale) for px, py in _C_ARROWS[kind]]
    draw.polygon(pts, fill=(22, 18, 6, 255))
    return img.resize(BUTTON_ICON_SIZE, Image.Resampling.LANCZOS)


def build_button_icon(kind: str) -> Image.Image:
    """An N64 controller button or D-pad icon for the help bar (AC-T16)."""
    if kind in _C_ARROWS:
        return _build_c_button_icon(kind)
    img = Image.new("RGBA", BUTTON_ICON_SIZE, (0, 0, 0, 0))
    w, h = BUTTON_ICON_SIZE
    draw = ImageDraw.Draw(img)
    if kind in _ROUND_BUTTONS:
        rim, body, shine, label, text_col = _ROUND_BUTTONS[kind]
        draw.ellipse((1, 1, w - 2, h - 2), fill=rim)
        draw.ellipse((2, 2, w - 4, h - 4), fill=body)
        draw.ellipse((6, 4, 15, 11), fill=shine)
        face = font(Face.SANS_BOLD, 16)
        bbox = draw.textbbox((0, 0), label, font=face)
        tx = (w - (bbox[2] - bbox[0])) / 2 - bbox[0]
        ty = (h - (bbox[3] - bbox[1])) / 2 - bbox[1]
        draw.text((tx, ty), label, font=face, fill=text_col)
    elif kind in _SHOULDER_BUTTONS:
        label = _SHOULDER_BUTTONS[kind]
        draw.rounded_rectangle((1, 5, w - 2, h - 6), radius=8, fill=(52, 56, 68))
        draw.rounded_rectangle((2, 6, w - 4, h - 8), radius=7, fill=(142, 148, 164))
        draw.rounded_rectangle((6, 7, w - 8, 11), radius=2, fill=(192, 198, 214))
        face = font(Face.SANS_BOLD, 14)
        bbox = draw.textbbox((0, 0), label, font=face)
        tx = (w - (bbox[2] - bbox[0])) / 2 - bbox[0]
        ty = (h - (bbox[3] - bbox[1])) / 2 - bbox[1] - 1
        draw.text((tx, ty), label, font=face, fill=(20, 22, 30))
    else:
        draw.rectangle((10, 1, 21, 30), fill=(44, 48, 58))
        draw.rectangle((1, 10, 30, 21), fill=(44, 48, 58))
        draw.rectangle((11, 2, 20, 29), fill=(116, 122, 138))
        draw.rectangle((2, 11, 29, 20), fill=(116, 122, 138))
        draw.ellipse((13, 13, 18, 18), fill=(86, 92, 106))
        ud = (245, 248, 255) if kind in {"dpad_all", "dpad_ud"} else (72, 76, 88)
        lr = (245, 248, 255) if kind in {"dpad_all", "dpad_lr"} else (72, 76, 88)
        draw.polygon([(15, 4), (12, 8), (19, 8)], fill=ud)
        draw.polygon([(15, 27), (12, 23), (19, 23)], fill=ud)
        draw.polygon([(4, 15), (8, 12), (8, 19)], fill=lr)
        draw.polygon([(27, 15), (23, 12), (23, 19)], fill=lr)
    return img


SUSPEND_DRAWER_SIZE: Final = (1280, 348)
SUSPEND_CART_SIZE: Final = (216, 143)
SUSPEND_CART_VARIANTS: Final = ("stock", "selected", "empty", "target", "mask")
_CART_OVERRIDES: Final[dict[str, dict[str, str]]] = {
    "stock": {},
    "selected": {
        "#1c1f26": "#ffffff",
        "#8b909a": "#76d2ff",
        "#b8beca": "#eefaff",
        "#767b86": "#53bced",
        "#50545e": "#2a8ec0",
        "#b6bcc8": "#e8f9ff",
        "#747984": "#4db4e6",
        "#32363e": "#1f78a8",
        "#b4bac6": "#d8f5ff",
        "#4e525c": "#2382b4",
        "#646975": "#4ec0f2",
        "#0a0c10": "#08121a",
    },
    "empty": {
        "#1c1f26": "#3e4452",
        "#8b909a": "#14171e",
        "#b8beca": "#4d5566",
        "#767b86": "#101319",
        "#50545e": "#2e333f",
        "#b6bcc8": "#454c5c",
        "#747984": "#11141b",
        "#32363e": "#2d323e",
        "#b4bac6": "#1c2029",
        "#4e525c": "#2f3542",
        "#646975": "#1f232c",
        "#0a0c10": "#090b0f",
    },
    "target": {
        "#1c1f26": "#6ad0ff",
        "#8b909a": "#16202b",
        "#b8beca": "#7ce0ff",
        "#767b86": "#121a24",
        "#50545e": "#4ab8eb",
        "#b6bcc8": "#7ce0ff",
        "#747984": "#121a24",
        "#32363e": "#3ca4d6",
        "#b4bac6": "#1a2836",
        "#4e525c": "#4ab8eb",
        "#646975": "#1e3244",
        "#0a0c10": "#090d12",
    },
    "mask": {
        "#1c1f26": "#000000",
        "#8b909a": "#000000",
        "#b8beca": "#000000",
        "#767b86": "#000000",
        "#50545e": "#000000",
        "#b6bcc8": "#000000",
        "#747984": "#000000",
        "#32363e": "#000000",
        "#b4bac6": "#000000",
        "#4e525c": "#000000",
        "#646975": "#000000",
        "#0a0c10": "#ffffff",
    },
}


def _make_suspend_cube_icon(size: int = 32) -> Image.Image:
    """Build the isometric blue suspend cube icon with downward white arrow."""
    scale = 4
    w = size * scale
    img = Image.new("RGBA", (w, w), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx = w // 2
    rim = (18, 36, 58, 255)
    top_pts = [
        (cx, round(w * 0.30)),
        (round(w * 0.86), round(w * 0.46)),
        (cx, round(w * 0.62)),
        (round(w * 0.14), round(w * 0.46)),
    ]
    left_pts = [
        (round(w * 0.14), round(w * 0.46)),
        (cx, round(w * 0.62)),
        (cx, round(w * 0.92)),
        (round(w * 0.14), round(w * 0.76)),
    ]
    right_pts = [
        (cx, round(w * 0.62)),
        (round(w * 0.86), round(w * 0.46)),
        (round(w * 0.86), round(w * 0.76)),
        (cx, round(w * 0.92)),
    ]
    draw.polygon(top_pts, fill=(145, 226, 255, 255), outline=rim)
    draw.polygon(left_pts, fill=(56, 168, 242, 255), outline=rim)
    draw.polygon(right_pts, fill=(24, 112, 196, 255), outline=rim)
    draw.rectangle(
        (cx - round(w * 0.10), round(w * 0.05), cx + round(w * 0.10), round(w * 0.28)),
        fill=(255, 255, 255, 255),
        outline=rim,
    )
    draw.polygon(
        [
            (cx - round(w * 0.25), round(w * 0.24)),
            (cx + round(w * 0.25), round(w * 0.24)),
            (cx, round(w * 0.46)),
        ],
        fill=(255, 255, 255, 255),
        outline=rim,
    )
    return img.resize((size, size), Image.Resampling.LANCZOS)


def build_suspend_drawer() -> Image.Image:
    """Build the 1280x348 ``NUS-006`` Suspend Point List drawer backdrop (AC-T13)."""
    drawer = render_svg(svg_asset("suspend_drawer.svg"), SUSPEND_DRAWER_SIZE)
    icon = _make_suspend_cube_icon(32)
    draw = ImageDraw.Draw(drawer, "RGBA")
    face = font(Face.SANS_BOLD, 27)
    title = "Suspend Point List"
    bbox = draw.textbbox((0, 0), title, font=face)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    total_w = icon.width + 12 + tw
    hx = int((SUSPEND_DRAWER_SIZE[0] - total_w) // 2)
    hy = 50
    drawer.alpha_composite(icon, (hx, hy - 3))
    draw.text(
        (hx + icon.width + 12 - bbox[0], hy + (icon.height - th) / 2 - bbox[1] - 2),
        title,
        font=face,
        fill=(226, 46, 56, 255),
    )
    return drawer


def build_suspend_cart(variant: str) -> Image.Image:
    """Render one ``NUS-006`` slot cartridge variant from ``n64_cartridge.svg`` (AC-T13)."""
    rendered = render_svg(
        svg_asset("n64_cartridge.svg"),
        SUSPEND_CART_SIZE,
        color_overrides=_CART_OVERRIDES[variant],
    )
    if variant != "mask":
        return rendered
    # Convert white pocket well -> alpha mask so screenshots clip cleanly to the inner arch.
    w, h = SUSPEND_CART_SIZE
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    red = rendered.getchannel("R")
    out.putalpha(red)
    return out


LIBRARY_TOAST_SIZE: Final = (336, 94)


def build_library_toast(mode: str) -> Image.Image:
    """Build the drop-down callout toast for ``'showcase'`` or ``'full'`` under the top bar."""
    w, h = LIBRARY_TOAST_SIZE
    is_showcase = mode == "showcase"
    label = "Showcase" if is_showcase else "Full Library"
    base_rgb = (235, 180, 34) if is_showcase else (62, 158, 248)
    hi_rgb = (255, 238, 150) if is_showcase else (178, 230, 255)
    rim_rgb = (74, 48, 8) if is_showcase else (14, 44, 86)

    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((8, 15, w - 9, h - 4), radius=19, fill=(0, 0, 0, 195))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=4))
    canvas.alpha_composite(shadow)

    card_w, card_h = w - 16, h - 20
    card = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
    draw_led_rounded_border(
        card,
        (0, 0, card_w - 1, card_h - 1),
        (base_rgb, hi_rgb, rim_rgb, (14, 18, 26)),
        geom=(5, 18),
        shadow=False,
    )
    cd = ImageDraw.Draw(card)
    cd.rounded_rectangle((7, 7, card_w - 8, card_h - 8), radius=13, fill=(18, 23, 34, 248))

    icon = make_icon_library("showcase" if is_showcase else "full").resize(
        (46, 46), Image.Resampling.LANCZOS
    )
    bx, by = 18, (card_h - 46) // 2
    cd.rounded_rectangle(
        (bx - 4, by - 4, bx + 50, by + 50),
        radius=11,
        fill=(28, 33, 44, 255),
        outline=(*base_rgb, 155),
        width=1,
    )
    card.alpha_composite(icon, (bx, by))

    face = font(Face.SANS_BOLD, 25)
    bbox = cd.textbbox((0, 0), label, font=face)
    tw_txt, th_txt = bbox[2] - bbox[0], bbox[3] - bbox[1]
    text_area_x0 = bx + 54
    tx = round(text_area_x0 + (card_w - 14 - text_area_x0 - tw_txt) / 2 - bbox[0])
    ty = round((card_h - th_txt) / 2 - bbox[1])
    cd.text((tx, ty), label, font=face, fill=(248, 250, 255, 255))

    canvas.alpha_composite(card, (8, 11))
    pd = ImageDraw.Draw(canvas)
    cx = w // 2
    pd.polygon([(cx, 1), (cx - 12, 13), (cx + 12, 13)], fill=(*hi_rgb, 255))
    pd.polygon([(cx, 5), (cx - 8, 16), (cx + 8, 16)], fill=(18, 23, 34, 255))
    return canvas
