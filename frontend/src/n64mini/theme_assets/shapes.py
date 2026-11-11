"""Low-poly N64-style primitives, SVG generators, and 4x supersampled SVG rasteriser."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, cast

from lxml import etree
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from n64mini.paths import brand_text, svg_asset
from n64mini.theme_assets.texture import RGB, Gradient, Tex, make_n64_surface_tex, scale_col

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

type Point = tuple[float, float]

OUTLINE_DARK: Final[RGB] = (22, 25, 34)
# Per-facet light multipliers, clockwise from the top edge (light from top-left).
_BOX_TEX: Final = Tex(16, 16, 12)
_LIGHT_RAISED: Final = (1.42, 1.12, 0.72, 0.48, 0.54, 0.82, 1.24, 1.52)
_LIGHT_RECESSED: Final = (0.52, 0.78, 1.18, 1.42, 1.35, 1.05, 0.68, 0.45)
_SVG_TOKEN_RE: Final = re.compile(
    r"[MmLlHhVvCcSsQqTtAaZz]|[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?"
)


def _octagon(x0: int, y0: int, x1: int, y1: int, chamf: int) -> list[Point]:
    return [
        (x0 + chamf, y0),
        (x1 - chamf, y0),
        (x1, y0 + chamf),
        (x1, y1 - chamf),
        (x1 - chamf, y1),
        (x0 + chamf, y1),
        (x0, y1 - chamf),
        (x0, y0 + chamf),
    ]


def draw_chiseled_box(  # noqa: PLR0913 - geometry + look; all keyword-only past the box
    canvas: Image.Image,
    box: tuple[int, int, int, int],
    *,
    chamf: int,
    bevel: int,
    colors: Gradient,
    tex: Tex = _BOX_TEX,
    shadow: bool = True,
    invert_bevel: bool = False,
) -> None:
    """An 8-sided plate with flat-shaded bevel facets and a textured face."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    outer = _octagon(x0, y0, x1, y1, chamf)
    inner = _octagon(x0 + bevel, y0 + bevel, x1 - bevel, y1 - bevel, max(2, int(chamf * 0.72)))

    if shadow:
        shadow_layer = canvas.copy()
        shadow_pts = [(px + 8, py + 8) for px, py in outer]
        ImageDraw.Draw(shadow_layer).polygon(shadow_pts, fill=(5, 7, 15))
        canvas.paste(Image.blend(canvas, shadow_layer, 0.55))

    face = make_n64_surface_tex(w, h, colors, tex)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).polygon([(px - x0, py - y0) for px, py in outer], fill=255)
    canvas.paste(face, (x0, y0), mask)

    light = _LIGHT_RECESSED if invert_bevel else _LIGHT_RAISED
    avg: RGB = (
        (colors.tl[0] + colors.br[0]) // 2,
        (colors.tl[1] + colors.br[1]) // 2,
        (colors.tl[2] + colors.br[2]) // 2,
    )
    draw = ImageDraw.Draw(canvas)
    for i in range(8):
        nxt = (i + 1) % 8
        draw.polygon(
            [outer[i], outer[nxt], inner[nxt], inner[i]],
            fill=scale_col(avg, light[i]),
            outline=OUTLINE_DARK,
        )
    draw.polygon(outer, outline=(14, 16, 22))
    draw.polygon(inner, outline=(28, 32, 42))


def _cubic(p0: Point, p1: Point, p2: Point, p3: Point, steps: int = 16) -> list[Point]:
    pts: list[Point] = []
    for i in range(1, steps + 1):
        t = i / steps
        mt = 1.0 - t
        x = mt**3 * p0[0] + 3 * mt**2 * t * p1[0] + 3 * mt * t**2 * p2[0] + t**3 * p3[0]
        y = mt**3 * p0[1] + 3 * mt**2 * t * p1[1] + 3 * mt * t**2 * p2[1] + t**3 * p3[1]
        pts.append((x, y))
    return pts


def _quad(p0: Point, p1: Point, p2: Point, steps: int = 12) -> list[Point]:
    pts: list[Point] = []
    for i in range(1, steps + 1):
        t = i / steps
        mt = 1.0 - t
        x = mt**2 * p0[0] + 2 * mt * t * p1[0] + t**2 * p2[0]
        y = mt**2 * p0[1] + 2 * mt * t * p1[1] + t**2 * p2[1]
        pts.append((x, y))
    return pts


def _vec_angle(ux: float, uy: float, vx: float, vy: float) -> float:
    dot = ux * vx + uy * vy
    mag = math.hypot(ux, uy) * math.hypot(vx, vy)
    ang = math.acos(max(-1.0, min(1.0, dot / mag))) if mag > 0 else 0.0
    return -ang if (ux * vy - uy * vx) < 0 else ang


def _arc(
    p0: Point,
    radii: Point,
    phi_deg: float,
    flags: tuple[bool, bool],
    p1: Point,
) -> list[Point]:
    steps = 16
    rx, ry = abs(radii[0]), abs(radii[1])
    if rx == 0 or ry == 0 or p0 == p1:
        return [p1]
    large_arc, sweep = flags
    phi = math.radians(phi_deg)
    cos_phi, sin_phi = math.cos(phi), math.sin(phi)
    dx2, dy2 = (p0[0] - p1[0]) / 2.0, (p0[1] - p1[1]) / 2.0
    x1p = cos_phi * dx2 + sin_phi * dy2
    y1p = -sin_phi * dx2 + cos_phi * dy2
    lam = (x1p**2) / (rx**2) + (y1p**2) / (ry**2)
    if lam > 1.0:
        scale = math.sqrt(lam)
        rx *= scale
        ry *= scale
    num = max(0.0, (rx**2 * ry**2) - (rx**2 * y1p**2) - (ry**2 * x1p**2))
    den = (rx**2 * y1p**2) + (ry**2 * x1p**2)
    rad = math.sqrt(num / den) if den > 0 else 0.0
    if large_arc == sweep:
        rad = -rad
    cxp = rad * (rx * y1p) / ry
    cyp = rad * (-ry * x1p) / rx
    cx = cos_phi * cxp - sin_phi * cyp + (p0[0] + p1[0]) / 2.0
    cy = sin_phi * cxp + cos_phi * cyp + (p0[1] + p1[1]) / 2.0
    theta1 = _vec_angle(1.0, 0.0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dtheta = _vec_angle((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dtheta > 0:
        dtheta -= 2 * math.pi
    elif sweep and dtheta < 0:
        dtheta += 2 * math.pi
    return [
        (
            cos_phi * (rx * math.cos(theta1 + dtheta * (i / steps)))
            - sin_phi * (ry * math.sin(theta1 + dtheta * (i / steps)))
            + cx,
            sin_phi * (rx * math.cos(theta1 + dtheta * (i / steps)))
            + cos_phi * (ry * math.sin(theta1 + dtheta * (i / steps)))
            + cy,
        )
        for i in range(1, steps + 1)
    ]


@dataclass(slots=True)
class _PathState:
    subpaths: list[list[Point]]
    cur: list[Point]
    cx: float = 0.0
    cy: float = 0.0
    sx: float = 0.0
    sy: float = 0.0
    last_ctrl: Point | None = None
    cmd: str = ""


def _step_line_cmd(st: _PathState, tokens: Sequence[str], i: int) -> int:
    cmd = st.cmd
    if cmd in ("M", "m"):
        x, y = float(tokens[i]), float(tokens[i + 1])
        st.cx = st.cx + x if cmd == "m" else x
        st.cy = st.cy + y if cmd == "m" else y
        st.sx, st.sy = st.cx, st.cy
        if st.cur:
            st.subpaths.append(st.cur)
        st.cur = [(st.cx, st.cy)]
        st.cmd = "l" if cmd == "m" else "L"
        st.last_ctrl = None
        return i + 2
    if cmd in ("L", "l"):
        x, y = float(tokens[i]), float(tokens[i + 1])
        st.cx = st.cx + x if cmd == "l" else x
        st.cy = st.cy + y if cmd == "l" else y
        st.cur.append((st.cx, st.cy))
        st.last_ctrl = None
        return i + 2
    if cmd in ("H", "h"):
        x = float(tokens[i])
        st.cx = st.cx + x if cmd == "h" else x
        st.cur.append((st.cx, st.cy))
        st.last_ctrl = None
        return i + 1
    y = float(tokens[i])
    st.cy = st.cy + y if cmd == "v" else y
    st.cur.append((st.cx, st.cy))
    st.last_ctrl = None
    return i + 1


def _step_curve_cmd(st: _PathState, tokens: Sequence[str], i: int) -> int:
    cmd = st.cmd
    if cmd in ("C", "c"):
        x1, y1, x2, y2, x, y = (float(tokens[i + k]) for k in range(6))
        p1 = (st.cx + x1, st.cy + y1) if cmd == "c" else (x1, y1)
        p2 = (st.cx + x2, st.cy + y2) if cmd == "c" else (x2, y2)
        p3 = (st.cx + x, st.cy + y) if cmd == "c" else (x, y)
        st.cur.extend(_cubic((st.cx, st.cy), p1, p2, p3))
        st.cx, st.cy = p3
        st.last_ctrl = p2
        return i + 6
    if cmd in ("S", "s"):
        x2, y2, x, y = (float(tokens[i + k]) for k in range(4))
        p1 = (
            (2 * st.cx - st.last_ctrl[0], 2 * st.cy - st.last_ctrl[1])
            if st.last_ctrl
            else (st.cx, st.cy)
        )
        p2 = (st.cx + x2, st.cy + y2) if cmd == "s" else (x2, y2)
        p3 = (st.cx + x, st.cy + y) if cmd == "s" else (x, y)
        st.cur.extend(_cubic((st.cx, st.cy), p1, p2, p3))
        st.cx, st.cy = p3
        st.last_ctrl = p2
        return i + 4
    if cmd in ("Q", "q"):
        x1, y1, x, y = (float(tokens[i + k]) for k in range(4))
        p1 = (st.cx + x1, st.cy + y1) if cmd == "q" else (x1, y1)
        p2 = (st.cx + x, st.cy + y) if cmd == "q" else (x, y)
        st.cur.extend(_quad((st.cx, st.cy), p1, p2))
        st.cx, st.cy = p2
        st.last_ctrl = p1
        return i + 4
    rx, ry, phi, la, sw, x, y = (float(tokens[i + k]) for k in range(7))
    p1 = (st.cx + x, st.cy + y) if cmd == "a" else (x, y)
    st.cur.extend(_arc((st.cx, st.cy), (rx, ry), phi, (int(la) != 0, int(sw) != 0), p1))
    st.cx, st.cy = p1
    st.last_ctrl = None
    return i + 7


def _parse_svg_path(d: str) -> list[list[Point]]:
    tokens = _SVG_TOKEN_RE.findall(d)
    st = _PathState(subpaths=[], cur=[])
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if tok[0].isalpha():
            st.cmd = tok
            i += 1
            if st.cmd in ("Z", "z"):
                if st.cur:
                    st.subpaths.append(st.cur)
                    st.cur = []
                st.cx, st.cy = st.sx, st.sy
                st.last_ctrl = None
                continue
        if st.cmd in ("M", "m", "L", "l", "H", "h", "V", "v"):
            i = _step_line_cmd(st, tokens, i)
        else:
            i = _step_curve_cmd(st, tokens, i)
    if st.cur:
        st.subpaths.append(st.cur)
    return st.subpaths


def _hex_rgb(col: str) -> RGB:
    c = col.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))


def _parse_transform(el: etree._Element) -> tuple[float, float, float, float]:
    tx, ty, sx, sy = 0.0, 0.0, 1.0, 1.0
    cur: etree._Element | None = el
    chain: list[str] = []
    while cur is not None:
        tr = cur.get("transform")
        if tr:
            chain.append(tr)
        cur = cur.getparent()
    for tr in reversed(chain):
        m_tr = re.search(r"translate\(\s*([+-]?[\d.]+)(?:[\s,]+([+-]?[\d.]+))?\s*\)", tr)
        if m_tr:
            tx += float(m_tr.group(1)) * sx
            ty += float(m_tr.group(2) or 0.0) * sy
        m_sc = re.search(r"scale\(\s*([+-]?[\d.]+)(?:[\s,]+([+-]?[\d.]+))?\s*\)", tr)
        if m_sc:
            s_x = float(m_sc.group(1))
            s_y = float(m_sc.group(2) if m_sc.group(2) is not None else s_x)
            sx *= s_x
            sy *= s_y
    return tx, ty, sx, sy


def render_svg(
    svg_path: Path,
    size: tuple[int, int],
    *,
    scale: int = 4,
    color_overrides: dict[str, str] | None = None,
) -> Image.Image:
    """Rasterise ``svg_path`` to an RGBA ``Image`` of ``size`` using 4x supersampling."""
    root = etree.parse(str(svg_path)).getroot()
    vb = [float(v) for v in (root.get("viewBox") or f"0 0 {size[0]} {size[1]}").split()]
    vx, vy, vw, vh = vb[0], vb[1], vb[2], vb[3]
    w_hi, h_hi = size[0] * scale, size[1] * scale
    canvas = Image.new("RGBA", (w_hi, h_hi), (0, 0, 0, 0))
    overrides = color_overrides or {}
    for el in root.iter("{http://www.w3.org/2000/svg}path"):
        if el.get("fill-opacity") == "0":
            continue
        raw_fill = el.get("fill") or "#ffffff"
        fill = overrides.get(raw_fill, raw_fill)
        d = el.get("d") or ""
        if not d or fill == "none":
            continue
        tx, ty, sx, sy = _parse_transform(el)
        subpaths = _parse_svg_path(d)
        mask = Image.new("L", (w_hi, h_hi), 0)
        for sp in subpaths:
            if len(sp) < 3:
                continue
            pts = [
                (((x * sx + tx - vx) / vw) * w_hi, ((y * sy + ty - vy) / vh) * h_hi) for x, y in sp
            ]
            poly = Image.new("L", (w_hi, h_hi), 0)
            ImageDraw.Draw(poly).polygon(pts, fill=255)
            mask = ImageChops.difference(mask, poly)
        color_layer = Image.new("RGBA", (w_hi, h_hi), (*_hex_rgb(fill), 255))
        canvas.paste(color_layer, (0, 0), mask)
    return canvas.resize(size, Image.Resampling.LANCZOS)


def _circle_path(cx: float, cy: float, r: float) -> str:
    """Closed SVG circular path centred at ``(cx, cy)`` with radius ``r``."""
    return (
        f"M {cx - r:.1f} {cy:.1f}"
        f" A {r:.1f} {r:.1f} 0 1 0 {cx + r:.1f} {cy:.1f}"
        f" A {r:.1f} {r:.1f} 0 1 0 {cx - r:.1f} {cy:.1f} Z"
    )


def build_front_bezel_svg(logo_svg: Path | None = None, brand: str | None = None) -> str:
    """Build the SVG for the black front console dome bezel with the brand wordmark."""
    src = logo_svg or svg_asset("nintendo64_logo.svg")
    label = (brand or brand_text()).strip()
    n64_tree = etree.parse(str(src)).getroot()
    wordmark_paths: list[tuple[str, str]] = []
    for el in n64_tree.iter("{http://www.w3.org/2000/svg}path"):
        fill = el.get("fill", "")
        d = el.get("d", "")
        if fill == "#7b7692":
            continue
        nums = [float(x) for x in re.findall(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?", d)]
        if len(nums) >= 2 and nums[1] < 120:
            bezel_fill = "#e4e8f2" if fill == "#0124a6" else fill
            wordmark_paths.append((bezel_fill, d))
    wm_elems = "\n    ".join(f'<path fill="{f}" d="{d}"/>' for f, d in wordmark_paths)
    s_out1 = (
        "M 22 16 Q 110 -7 198 16 Q 212 20 208 35"
        " L 197 68 Q 193 79 179 80 L 41 80 Q 27 79 23 68 L 12 35 Q 8 20 22 16 Z"
    )
    s_out2 = (
        "M 23 17 Q 110 -5 197 17 Q 210 21 206 35"
        " L 195 67 Q 192 77 178 78 L 42 78 Q 28 77 25 67 L 14 35 Q 10 21 23 17 Z"
    )
    s_dome = (
        "M 25 18.5 Q 110 -3 195 18.5 Q 207 22.5 203 35"
        " L 193 66 Q 190 75 177 76 L 43 76 Q 30 75 27 66 L 17 35 Q 13 22.5 25 18.5 Z"
    )
    s_arch = "M 29 20.5 Q 110 0 191 20.5 Q 200 23.5 198 32 Q 110 14 22 32 Q 20 23.5 29 20.5 Z"
    lines = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="220" height="82" viewBox="0 0 220 82">',
        "  <!-- Outer recessed seam in the charcoal N64 console shell -->",
        f'  <path fill="#14161c" d="{s_out1}"/>',
        f'  <path fill="#484c58" d="{s_out2}"/>',
        "  <!-- Black N64 front console dome bezel -->",
        f'  <path fill="#0a0b0e" d="{s_dome}"/>',
        "  <!-- Subtle glossy top arch reflection -->",
        f'  <path fill="#181b22" d="{s_arch}"/>',
        f"  <!-- '{label}' wordmark centred in the black bezel -->",
        '  <g transform="translate(34.49, 29.12) scale(0.205, 0.22)">',
        f"    {wm_elems}",
        "  </g>",
        "</svg>",
        "",
    )
    return "\n".join(lines)


def build_controller_port_svg() -> str:
    """Build the SVG for an N64 controller port traced from ``console_front_profile.jpg``."""
    p_seam = _circle_path(40.0, 40.0, 38.0)
    p_bevel = _circle_path(40.0, 40.0, 36.0)
    p_top_hi = _circle_path(40.0, 39.2, 35.0)
    p_face = _circle_path(40.0, 40.0, 34.4)
    p_cav_lip = (
        "M 15.6 50.2 A 26.2 26.2 0 1 1 64.4 50.2"
        " Q 63.2 54.8 58.8 54.8 L 21.2 54.8 Q 16.8 54.8 15.6 50.2 Z"
    )
    p_cav = (
        "M 16.3 49.5 A 25.5 25.5 0 1 1 63.7 49.5"
        " Q 62.5 53.3 58.5 53.3 L 21.5 53.3 Q 17.5 53.3 16.3 49.5 Z"
    )
    p_isl_bevel = (
        "M 20.1 47.0 A 21.1 21.1 0 1 1 59.9 47.0"
        " Q 58.9 49.3 55.6 49.3 L 24.4 49.3 Q 21.1 49.3 20.1 47.0 Z"
    )
    p_isl_face = (
        "M 20.9 46.5 A 20.2 20.2 0 1 1 59.1 46.5"
        " Q 58.2 48.4 55.2 48.4 L 24.8 48.4 Q 21.8 48.4 20.9 46.5 Z"
    )
    pin_xs = (27.4, 40.0, 52.6)
    hole_paths = [
        f'  <path fill="#585d68" d="{_circle_path(px, 40.0, 5.7)}"/>\n'
        f'  <path fill="#0b0d12" d="{_circle_path(px, 40.0, 5.1)}"/>\n'
        f'  <path fill="#8c7e58" d="{_circle_path(px, 40.0, 2.0)}"/>\n'
        f'  <path fill="#dfd3a8" d="{_circle_path(px, 39.8, 1.3)}"/>'
        for px in pin_xs
    ]
    lines = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="78" height="78" viewBox="0 0 80 80">',
        "  <!-- Outer circular console recess shadow -->",
        f'  <path fill="#111318" d="{p_seam}"/>',
        "  <!-- Light-grey circular port housing -->",
        f'  <path fill="#969ca8" d="{p_bevel}"/>',
        f'  <path fill="#e2e6ec" d="{p_top_hi}"/>',
        f'  <path fill="#d0d5dc" d="{p_face}"/>',
        "  <!-- D-shaped recessed plug cavity with lower chamfer highlight -->",
        f'  <path fill="#e4e8ee" d="{p_cav_lip}"/>',
        f'  <path fill="#0e1015" d="{p_cav}"/>',
        "  <!-- Inner D-shaped light-grey connector island -->",
        f'  <path fill="#9298a4" d="{p_isl_bevel}"/>',
        f'  <path fill="#cfd4dc" d="{p_isl_face}"/>',
        "  <!-- 3 circular black pin wells with brass contact pins -->",
        *hole_paths,
        "</svg>",
        "",
    )
    return "\n".join(lines)


def build_n64_cartridge_svg() -> str:
    """Build the stock 348x230 ``NUS-006`` N64 Game Pak front-shell SVG."""
    lines = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="348" height="230" viewBox="0 0 348 230">',
        "  <!-- NUS-006 outer shell rim, smooth body, and top arch highlight -->",
        (
            '  <path fill="#1c1f26" d="M 8 38 Q 174 -12 340 38 Q 345 40 345 46 L 345 222'
            ' Q 345 227 340 227 L 8 227 Q 3 227 3 222 L 3 46 Q 3 40 8 38 Z"/>'
        ),
        (
            '  <path fill="#8b909a" d="M 10 40 Q 174 -9 338 40 Q 342 42 342 46 L 342 221'
            ' Q 342 224 338 224 L 10 224 Q 6 224 6 221 L 6 46 Q 6 42 10 40 Z"/>'
        ),
        (
            '  <path fill="#b8beca" d="M 10 40 Q 174 -9 338 40 Q 341 41 342 45'
            ' Q 174 -3 6 45 Q 7 41 10 40 Z"/>'
        ),
        "  <!-- Smooth outer flank curvature shading and lower corner bevel steps -->",
        '  <path fill="#767b86" d="M 6 46 L 22 41 L 22 224 L 10 224 Q 6 224 6 221 Z"/>',
        '  <path fill="#767b86" d="M 326 41 L 342 46 L 342 221 Q 342 224 338 224 L 326 224 Z"/>',
        (
            '  <path fill="#50545e" d="M 6 156 L 27 170 Q 29 172 29 175 L 29 224'
            ' L 10 224 Q 6 224 6 221 Z"/>'
        ),
        '  <path fill="#b6bcc8" d="M 6 154 L 28 168 L 27 171 L 6 157 Z"/>',
        '  <path fill="#747984" d="M 8 159 L 26 171 L 26 222 L 8 222 Z"/>',
        (
            '  <path fill="#50545e" d="M 342 156 L 321 170 Q 319 172 319 175 L 319 224'
            ' L 338 224 Q 342 224 342 221 Z"/>'
        ),
        '  <path fill="#b6bcc8" d="M 342 154 L 320 168 L 321 171 L 342 157 Z"/>',
        '  <path fill="#747984" d="M 340 159 L 322 171 L 322 222 L 340 222 Z"/>',
        "  <!-- Full-height vertical NUS-006 seam grooves -->",
        '  <path fill="#32363e" d="M 68 28 L 71 27 L 71 224 L 68 224 Z"/>',
        '  <path fill="#b4bac6" d="M 71 27 L 73 27 L 73 224 L 71 224 Z"/>',
        '  <path fill="#32363e" d="M 277 27 L 280 28 L 280 224 L 277 224 Z"/>',
        '  <path fill="#b4bac6" d="M 275 27 L 277 27 L 277 224 L 275 224 Z"/>',
        "  <!-- Arch-topped recessed central label pocket -->",
        (
            '  <path fill="#4e525c" d="M 90 26 Q 174 7 258 26 Q 265 28 265 35 L 265 198'
            ' Q 265 205 258 205 L 90 205 Q 83 205 83 198 L 83 35 Q 83 28 90 26 Z"/>'
        ),
        (
            '  <path fill="#646975" d="M 91 29 Q 174 10 257 29 Q 263 31 263 37 L 263 197'
            ' Q 263 203 257 203 L 91 203 Q 85 203 85 197 L 85 37 Q 85 31 91 29 Z"/>'
        ),
        (
            '  <path fill="#0a0c10" d="M 92 31 Q 174 12 256 31 Q 261 33 261 38 L 261 196'
            ' Q 261 201 256 201 L 92 201 Q 87 201 87 196 L 87 38 Q 87 33 92 31 Z"/>'
        ),
        "</svg>",
        "",
    )
    return "\n".join(lines)


def build_suspend_drawer_svg(width: int = 1280, height: int = 348) -> str:
    """Build the 1280x348 ``NUS-006`` Suspend Point List drawer SVG with parallel inner arch."""
    lines = (
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"'
            f' viewBox="0 0 {width} {height}">'
        ),
        "  <!-- Outer NUS-006 cartridge drawer crown and side flanks -->",
        (
            f'  <path fill="#181b22" d="M 18 61 Q 640 -43 1262 61 Q 1272 64 1272 75 L 1272 {height}'
            f' L 8 {height} L 8 75 Q 8 64 18 61 Z"/>'
        ),
        (
            f'  <path fill="#808692" d="M 21 64 Q 640 -40 1259 64 Q 1268 67 1268 77 L 1268 {height}'
            f' L 12 {height} L 12 77 Q 12 67 21 64 Z"/>'
        ),
        (
            '  <path fill="#b6bcc8" d="M 21 64 Q 640 -40 1259 64 Q 1266 66 1268 72'
            ' Q 640 -32 12 72 Q 14 66 21 64 Z"/>'
        ),
        f'  <path fill="#6c727e" d="M 12 74 L 40 65 L 40 {height} L 12 {height} Z"/>',
        f'  <path fill="#6c727e" d="M 1240 65 L 1268 74 L 1268 {height} L 1240 {height} Z"/>',
        (
            f'  <path fill="#4e535e" d="M 12 232 L 50 256 Q 54 259 54 264 L 54 {height}'
            f' L 12 {height} Z"/>'
        ),
        '  <path fill="#b6bcc8" d="M 12 229 L 52 254 L 50 258 L 12 233 Z"/>',
        f'  <path fill="#686e7a" d="M 15 237 L 49 259 L 49 {height} L 15 {height} Z"/>',
        (
            f'  <path fill="#4e535e" d="M 1268 232 L 1230 256 Q 1226 259 1226 264 L 1226 {height}'
            f' L 1268 {height} Z"/>'
        ),
        '  <path fill="#b6bcc8" d="M 1268 229 L 1228 254 L 1230 258 L 1268 233 Z"/>',
        f'  <path fill="#686e7a" d="M 1265 237 L 1231 259 L 1231 {height} L 1265 {height} Z"/>',
        f'  <path fill="#2e323b" d="M 74 56 L 78 55 L 78 {height} L 74 {height} Z"/>',
        f'  <path fill="#b2b8c4" d="M 78 55 L 81 55 L 81 {height} L 78 {height} Z"/>',
        f'  <path fill="#2e323b" d="M 1202 55 L 1206 56 L 1206 {height} L 1202 {height} Z"/>',
        f'  <path fill="#b2b8c4" d="M 1199 55 L 1202 55 L 1202 {height} L 1199 {height} Z"/>',
        "  <!-- Concentric arch-topped recessed black pocket -->",
        (
            f'  <path fill="#484d58" d="M 106 65 Q 640 -11 1174 65 Q 1188 68 1188 81'
            f" L 1188 {height - 16} Q 1188 {height - 8} 1178 {height - 8} L 102 {height - 8}"
            f' Q 92 {height - 8} 92 {height - 16} L 92 81 Q 92 68 106 65 Z"/>'
        ),
        (
            f'  <path fill="#606674" d="M 107 68 Q 640 -8 1173 68 Q 1185 71 1185 83'
            f" L 1185 {height - 18} Q 1185 {height - 10} 1176 {height - 10} L 104 {height - 10}"
            f' Q 95 {height - 10} 95 {height - 18} L 95 83 Q 95 71 107 68 Z"/>'
        ),
        (
            f'  <path fill="#07090e" d="M 108 70 Q 640 -6 1172 70 Q 1182 73 1182 84'
            f" L 1182 {height - 20} Q 1182 {height - 13} 1173 {height - 13} L 107 {height - 13}"
            f' Q 98 {height - 13} 98 {height - 20} L 98 84 Q 98 73 108 70 Z"/>'
        ),
        "</svg>",
        "",
    )
    return "\n".join(lines)


def _ring_mask(w: int, h: int, bw: int, radius: int) -> tuple[Image.Image, Image.Image]:
    """Return ``(m_ring, m_inner)`` L-mode masks for a rounded rectangle border of width ``bw``."""
    m_outer = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m_outer).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
    m_inner = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m_inner).rounded_rectangle(
        (bw, bw, w - 1 - bw, h - 1 - bw),
        radius=max(radius - bw, 4),
        fill=255,
    )
    m_ring = ImageChops.subtract(m_outer, m_inner)
    return m_ring, m_inner


def _on_mask(mask: Image.Image, x: int, y: int) -> bool:
    if 0 <= x < mask.width and 0 <= y < mask.height:
        val = mask.getpixel((x, y))
        return isinstance(val, (int, float)) and val > 127
    return False


def _is_inner_ring_pixel(m_ring: Image.Image, x: int, y: int) -> bool:
    return (
        _on_mask(m_ring, x - 1, y)
        and _on_mask(m_ring, x + 1, y)
        and _on_mask(m_ring, x, y - 1)
        and _on_mask(m_ring, x, y + 1)
    )


def _top_led_blend(x: int, y: int, w: int, base_rgb: RGB, hi_rgb: RGB) -> tuple[int, int, int, int]:
    """Blend ``base_rgb`` toward ``hi_rgb`` along the single top-edge LED highlight strip."""
    t = 0.0
    if y <= 4 and 10 <= x <= w - 11:
        edge_dist = min(x - 10, (w - 11) - x)
        t = min(1.0, edge_dist / 10.0)
    return (
        round(base_rgb[0] * (1.0 - t) + hi_rgb[0] * t),
        round(base_rgb[1] * (1.0 - t) + hi_rgb[1] * t),
        round(base_rgb[2] * (1.0 - t) + hi_rgb[2] * t),
        255,
    )


def draw_led_rounded_border(
    canvas: Image.Image,
    box: tuple[int, int, int, int],
    palette: tuple[RGB, RGB, RGB, RGB],
    *,
    geom: tuple[int, int] = (6, 16),
    shadow: bool = False,
) -> None:
    """Draw a rounded border with a single top-edge LED highlight strip (``highlight_1``)."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0 + 1, y1 - y0 + 1
    bw, radius = geom
    base_rgb, hi_rgb, rim_rgb, interior_rgb = palette
    m_ring, m_inner = _ring_mask(w, h, bw, radius)
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for y in range(h):
        for x in range(w):
            if _on_mask(m_ring, x, y):
                color = (
                    _top_led_blend(x, y, w, base_rgb, hi_rgb)
                    if _is_inner_ring_pixel(m_ring, x, y)
                    else (*rim_rgb, 255)
                )
                layer.putpixel((x, y), color)
            elif _on_mask(m_inner, x, y):
                layer.putpixel((x, y), (*interior_rgb, 255))
            elif shadow and (_on_mask(m_ring, x, y - 2) or _on_mask(m_ring, x - 1, y - 2)):
                layer.putpixel((x, y), (10, 12, 18, 185))
    canvas.paste(layer, (x0, y0), layer)


def _led_pixel_color(
    base_mask: Image.Image, xy: tuple[int, int], fill_rgb: RGB, hi_rgb: RGB, rim_rgb: RGB
) -> tuple[int, int, int, int]:
    x, y = xy
    if not _is_inner_ring_pixel(base_mask, x, y):
        return (*rim_rgb, 255)
    top_dist = 0
    for dy in range(1, 5):
        if not _on_mask(base_mask, x, y - dy):
            top_dist = dy
            break
    if top_dist in (2, 3) and _on_mask(base_mask, x - 2, y) and _on_mask(base_mask, x + 2, y):
        return (*hi_rgb, 240)
    return (*fill_rgb, 255)


def apply_led_style_finish(
    base_mask: Image.Image,
    fill_rgb: RGB,
    hi_rgb: RGB,
    rim_rgb: RGB,
) -> Image.Image:
    """Render an L-mode icon silhouette with drop shadow, 1px dark rim and upper LED highlight."""
    mw, mh = base_mask.size
    out = Image.new("RGBA", (mw, mh), (0, 0, 0, 0))
    for y in range(mh):
        for x in range(mw):
            if _on_mask(base_mask, x, y):
                out.putpixel((x, y), _led_pixel_color(base_mask, (x, y), fill_rgb, hi_rgb, rim_rgb))
            elif _on_mask(base_mask, x, y - 2) or _on_mask(base_mask, x - 1, y - 3):
                out.putpixel((x, y), (14, 16, 20, 195))
    return out


def make_icon_cog() -> Image.Image:
    """Build the 64x64 white/light-grey settings cog icon for the top-bar pocket."""
    m_cog = Image.new("L", (64, 64), 0)
    d_cog = ImageDraw.Draw(m_cog)
    cx, cy_l = 32, 32
    r_body, r_tooth, r_hole = 14, 19, 6
    for i in range(8):
        ang = i * (math.pi / 4)
        dx, dy = math.cos(ang), math.sin(ang)
        px, py = -dy, dx
        tw = 3.8
        poly = [
            (round(cx + dx * (r_body - 2) + px * tw), round(cy_l + dy * (r_body - 2) + py * tw)),
            (
                round(cx + dx * r_tooth + px * (tw * 0.8)),
                round(cy_l + dy * r_tooth + py * (tw * 0.8)),
            ),
            (
                round(cx + dx * r_tooth - px * (tw * 0.8)),
                round(cy_l + dy * r_tooth - py * (tw * 0.8)),
            ),
            (round(cx + dx * (r_body - 2) - px * tw), round(cy_l + dy * (r_body - 2) - py * tw)),
        ]
        d_cog.polygon(poly, fill=255)
    d_cog.ellipse((cx - r_body, cy_l - r_body, cx + r_body, cy_l + r_body), fill=255)
    d_cog.ellipse((cx - r_hole, cy_l - r_hole, cx + r_hole, cy_l + r_hole), fill=0)
    return apply_led_style_finish(
        m_cog,
        fill_rgb=(210, 216, 226),
        hi_rgb=(252, 254, 255),
        rim_rgb=(42, 46, 54),
    )


def _fanned_card_layer(
    angle: float,
    dx: int,
    dy: int,
    colors: tuple[RGB, RGB, RGB],
) -> Image.Image:
    """Render one rotated 4x-supersampled game card layer for ``make_icon_library('full')``."""
    scale = 4
    hi = Image.new("L", (64 * scale, 64 * scale), 0)
    ImageDraw.Draw(hi).rounded_rectangle(
        (20 * scale, 15 * scale, 44 * scale, 48 * scale),
        radius=4 * scale,
        fill=255,
    )
    rotated = hi.rotate(angle, resample=Image.Resampling.BICUBIC, center=(32 * scale, 44 * scale))
    lo = rotated.resize((64, 64), Image.Resampling.LANCZOS)
    shifted = Image.new("L", (64, 64), 0)
    shifted.paste(lo, (dx, dy))
    mask = shifted.point([255 if v >= 112 else 0 for v in range(256)])
    return apply_led_style_finish(mask, *colors)


def make_icon_library(mode: str = "showcase") -> Image.Image:
    """Build the 64x64 Showcase (Gold Star) or Full Library (Fanned 3-Card Hand) icon."""
    if mode == "showcase":
        scale = 4
        cx, cy = 32.0 * scale, 33.0 * scale
        hi = Image.new("L", (64 * scale, 64 * scale), 0)
        pts: list[tuple[float, float]] = []
        for idx in range(10):
            ang = -math.pi / 2 + idx * (math.pi / 5)
            radius = (20.5 if idx % 2 == 0 else 9.2) * scale
            pts.append((cx + math.cos(ang) * radius, cy + math.sin(ang) * radius))
        ImageDraw.Draw(hi).polygon(pts, fill=255)
        hi = hi.filter(ImageFilter.GaussianBlur(radius=2.2))
        hi = hi.point([255 if v >= 120 else 0 for v in range(256)])
        mask = hi.resize((64, 64), Image.Resampling.LANCZOS)
        mask = mask.point([255 if v >= 112 else 0 for v in range(256)])
        return apply_led_style_finish(mask, (245, 190, 36), (255, 242, 162), (98, 64, 8))
    out = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    out.alpha_composite(
        _fanned_card_layer(20.0, -6, 1, ((42, 114, 192), (128, 188, 242), (14, 42, 78)))
    )
    out.alpha_composite(
        _fanned_card_layer(-20.0, 6, 1, ((48, 132, 218), (148, 206, 255), (16, 48, 88)))
    )
    out.alpha_composite(
        _fanned_card_layer(0.0, 0, -1, ((62, 158, 248), (178, 230, 255), (18, 56, 104)))
    )
    return out


def make_icon_music(*, active: bool = True) -> Image.Image:
    """Build the 64x64 background music note icon (emerald green when active, grey when muted)."""
    cx, cy_l = 32, 32
    m_mus = Image.new("L", (64, 64), 0)
    d_mus = ImageDraw.Draw(m_mus)
    d_mus.ellipse((cx - 16, cy_l + 4, cx - 3, cy_l + 15), fill=255)
    d_mus.ellipse((cx + 2, cy_l + 1, cx + 15, cy_l + 12), fill=255)
    d_mus.rectangle((cx - 7, cy_l - 14, cx - 3, cy_l + 9), fill=255)
    d_mus.rectangle((cx + 11, cy_l - 17, cx + 15, cy_l + 6), fill=255)
    d_mus.polygon(
        [(cx - 7, cy_l - 13), (cx + 15, cy_l - 17), (cx + 15, cy_l - 10), (cx - 7, cy_l - 6)],
        fill=255,
    )
    if active:
        return apply_led_style_finish(
            m_mus,
            fill_rgb=(46, 204, 96),
            hi_rgb=(162, 250, 188),
            rim_rgb=(16, 82, 36),
        )
    d_mus.line([(cx - 16, cy_l - 14), (cx + 16, cy_l + 14)], fill=0, width=7)
    d_mus.line([(cx - 16, cy_l - 14), (cx + 16, cy_l + 14)], fill=255, width=4)
    return apply_led_style_finish(
        m_mus,
        fill_rgb=(112, 118, 130),
        hi_rgb=(168, 174, 186),
        rim_rgb=(38, 42, 50),
    )


def draw_power_led(img: Image.Image, cx: int, cy: int, w: int = 52, h: int = 16) -> None:
    """Draw the glowing red pill Power LED in a recessed dark socket at ``(cx, cy)``."""
    draw = ImageDraw.Draw(img, "RGBA")
    r = h // 2
    draw.rounded_rectangle(
        (cx - w // 2 - 4, cy - h // 2 - 3, cx + w // 2 + 4, cy + h // 2 + 3),
        radius=r + 3,
        fill=(24, 26, 30, 255),
        outline=(88, 94, 104, 200),
        width=1,
    )
    draw.rounded_rectangle(
        (cx - w // 2, cy - h // 2, cx + w // 2, cy + h // 2),
        radius=r,
        fill=(242, 48, 82, 255),
        outline=(120, 12, 18, 255),
        width=1,
    )
    draw.rounded_rectangle(
        (cx - w // 2 + 5, cy - h // 2 + 2, cx + w // 2 - 5, cy - 1),
        radius=max(r - 2, 1),
        fill=(255, 165, 185, 230),
    )


def _wordmark_alpha(r: int, g: int, b: int) -> int:
    lum = max(r, g, b)
    redness = r - max(g, b)
    if lum <= 72 and redness <= 45:
        return 0
    alpha = min(255, max(0, int((lum - 52) * 2.2)))
    if redness > 40:
        alpha = min(255, max(alpha, int((redness - 30) * 2.6)))
    return alpha if alpha > 20 else 0


def extract_nintendo64_wordmark(
    source_img: Image.Image, crop_box: tuple[int, int, int, int] = (28, 22, 196, 53)
) -> Image.Image:
    """Extract the 'NINTENDO 64' wordmark (white NINTENDO + red 64) without its black bezel."""
    crop = source_img.crop(crop_box).convert("RGBA")
    cw, ch = crop.size
    out = Image.new("RGBA", (cw + 8, ch + 8), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (cw + 8, ch + 8), (0, 0, 0, 0))
    for y in range(ch):
        for x in range(cw):
            r, g, b, a = cast("tuple[int, int, int, int]", crop.getpixel((x, y)))
            alpha = a if a < 255 else _wordmark_alpha(r, g, b)
            if alpha > 0:
                out.putpixel((x + 4, y + 4), (r, g, b, alpha))
                shadow.putpixel((x + 6, y + 6), (14, 16, 20, int(alpha * 0.85)))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=1.2))
    shadow.alpha_composite(out)
    return shadow
