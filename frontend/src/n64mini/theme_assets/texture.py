"""N64 RDP-style surface textures: tiny TMEM texture, 3-point filter, RGBA5551 dither."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Final

from PIL import Image

type RGB = tuple[int, int, int]

# Ordered-dither offsets, as the VI/RDP 4x4 "magic square" pattern.
MAGIC_4X4: Final[tuple[tuple[float, float, float, float], ...]] = (
    (-3.5, 0.5, -2.5, 1.5),
    (0.5, -3.5, 1.5, -2.5),
    (-0.5, 3.5, -1.5, 2.5),
    (3.5, -0.5, 2.5, -1.5),
)


@dataclass(frozen=True, slots=True)
class Gradient:
    """Corner colours of a 4-corner gradient."""

    tl: RGB
    tr: RGB
    bl: RGB
    br: RGB

    @classmethod
    def vertical(cls, top: RGB, bottom: RGB) -> Gradient:
        """Top-to-bottom gradient (left and right corners equal)."""
        return cls(top, top, bottom, bottom)


@dataclass(frozen=True, slots=True)
class Tex:
    """TMEM texture size (texels) and per-texel noise grain."""

    w: int = 20
    h: int = 20
    grain: int = 14


def clamp_byte(value: int) -> int:
    """Clamp ``value`` to 0..255."""
    return max(0, min(255, value))


def scale_col(color: RGB, factor: float) -> RGB:
    """Multiply each channel by ``factor``, clamped to a byte."""
    return (
        clamp_byte(int(color[0] * factor)),
        clamp_byte(int(color[1] * factor)),
        clamp_byte(int(color[2] * factor)),
    )


def _bilinear(corners: tuple[RGB, RGB, RGB, RGB], vx: float, vy: float, channel: int) -> int:
    tl, tr, bl, br = corners
    return int(
        (1 - vx) * (1 - vy) * tl[channel]
        + vx * (1 - vy) * tr[channel]
        + (1 - vx) * vy * bl[channel]
        + vx * vy * br[channel]
    )


def _tmem_texture(
    corners: tuple[RGB, RGB, RGB, RGB], tmem_w: int, tmem_h: int, grain: int, seed: int
) -> list[list[RGB]]:
    """The small source texture, as rows of RGB, with per-texel noise grain."""
    rng = random.Random(seed)  # noqa: S311 - visual noise, not crypto
    rows: list[list[RGB]] = []
    for y in range(tmem_h):
        vy = y / max(1, tmem_h - 1)
        row: list[RGB] = []
        for x in range(tmem_w):
            vx = x / max(1, tmem_w - 1)
            n = rng.randint(-grain, grain) if grain > 0 else 0
            row.append(
                (
                    clamp_byte(_bilinear(corners, vx, vy, 0) + n),
                    clamp_byte(_bilinear(corners, vx, vy, 1) + n),
                    clamp_byte(_bilinear(corners, vx, vy, 2) + n),
                )
            )
        rows.append(row)
    return rows


DEFAULT_TEX: Final = Tex()


def make_n64_surface_tex(
    w: int, h: int, colors: Gradient, tex: Tex = DEFAULT_TEX, *, dither_str: float = 1.4
) -> Image.Image:
    """Render a ``w``x``h`` surface the way the N64 RDP would sample a tiny texture.

    Upscales a ``tex.w``x``tex.h`` gradient texture with 3-point (triangle)
    filtering, then quantises to 5 bits per channel with 4x4 ordered dither.
    """
    w, h = max(1, w), max(1, h)
    # Seed from a hash of int tuples: deterministic in CPython (no hash
    # randomisation for ints), which keeps the generated PNGs reproducible.
    seed = abs(hash((colors.tl, colors.br, w, h, tex.w))) % 100000
    tmem_w, tmem_h = tex.w, tex.h
    texels = _tmem_texture(
        (colors.tl, colors.tr, colors.bl, colors.br), tmem_w, tmem_h, tex.grain, seed
    )

    out: list[RGB] = []
    scale_x = (tmem_w - 1.001) / max(1, w - 1)
    scale_y = (tmem_h - 1.001) / max(1, h - 1)
    for y in range(h):
        gy = y * scale_y
        y0 = int(gy)
        y1 = min(tmem_h - 1, y0 + 1)
        fy = gy - y0
        row_m = MAGIC_4X4[(y // 2) & 3]
        for x in range(w):
            gx = x * scale_x
            x0 = int(gx)
            x1 = min(tmem_w - 1, x0 + 1)
            fx = gx - x0
            t00, t10, t01 = texels[y0][x0], texels[y0][x1], texels[y1][x0]
            if fx + fy < 1.0:
                r = t00[0] + fx * (t10[0] - t00[0]) + fy * (t01[0] - t00[0])
                g = t00[1] + fx * (t10[1] - t00[1]) + fy * (t01[1] - t00[1])
                b = t00[2] + fx * (t10[2] - t00[2]) + fy * (t01[2] - t00[2])
            else:
                t11 = texels[y1][x1]
                omx, omy = 1.0 - fx, 1.0 - fy
                r = t11[0] + omx * (t01[0] - t11[0]) + omy * (t10[0] - t11[0])
                g = t11[1] + omx * (t01[1] - t11[1]) + omy * (t10[1] - t11[1])
                b = t11[2] + omx * (t01[2] - t11[2]) + omy * (t10[2] - t11[2])
            d = row_m[(x // 2) & 3] * dither_str
            out.append((_quantise5(r + d), _quantise5(g + d), _quantise5(b + d)))

    image = Image.new("RGB", (w, h))
    image.putdata(out)
    return image


def _quantise5(value: float) -> int:
    """8-bit -> 5-bit -> 8-bit, matching RGBA5551 framebuffer precision."""
    q = max(0, min(31, int(value // 8)))
    return int(q * 255 / 31)
